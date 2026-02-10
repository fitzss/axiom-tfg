"""FastAPI application — web UI + REST API for axiom-tfg."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from axiom_tfg.evidence import run_gates, write_evidence
from axiom_tfg.models import TaskSpec

from axiom_server import ai
from axiom_server.db import RunStore

# ── configurable paths ────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AXIOM_DATA_DIR", "data"))
RUNS_DIR = DATA_DIR / "runs"
EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
PUBLIC_BASE_URL = os.environ.get("AXIOM_PUBLIC_BASE_URL", "").rstrip("/")

# ── app setup ─────────────────────────────────────────────────────────────

app = FastAPI(title="axiom-tfg", version="0.1.0")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

store = RunStore(DATA_DIR / "axiom.db")

# ── health ────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ── web UI ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    runs = store.list_recent(limit=50)
    return templates.TemplateResponse(
        request, "index.html", {"runs": runs, "ai_enabled": ai.is_available()},
    )


# ── API: create run ──────────────────────────────────────────────────────


@app.post("/runs")
async def create_run(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    text = body.decode("utf-8")

    # Parse input — accept YAML (text/plain, application/x-yaml) or JSON.
    try:
        if "json" in content_type:
            raw = json.loads(text)
        else:
            raw = yaml.safe_load(text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Parse error: {exc}")

    # Validate against TaskSpec.
    try:
        spec = TaskSpec.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    # Run the gate pipeline.
    packet = run_gates(spec)

    # Persist evidence to disk.
    run_id = uuid.uuid4().hex[:12]
    evidence_dir = RUNS_DIR / run_id
    evidence_path = write_evidence(packet, RUNS_DIR)
    # write_evidence writes to <out>/<task_id>/evidence.json — we also want
    # a deterministic path keyed by run_id, so symlink or copy.
    run_evidence = evidence_dir / "evidence.json"
    if not run_evidence.exists():
        evidence_dir.mkdir(parents=True, exist_ok=True)
        run_evidence.write_text(
            json.dumps(packet.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )

    # Derive top fix summary + structured patch for the UI.
    top_fix: str | None = None
    top_fix_patch: dict | None = None
    if packet.counterfactual_fixes:
        f = packet.counterfactual_fixes[0]
        top_fix = f.type.value
        if f.type.value == "MOVE_TARGET" and f.proposed_patch:
            new_xyz = f.proposed_patch.get("projected_target_xyz")
            if new_xyz:
                top_fix_patch = {"kind": "MOVE_TARGET", "new_xyz": new_xyz}
        elif f.type.value == "MOVE_BASE" and f.proposed_patch:
            new_xyz = f.proposed_patch.get("suggested_base_xyz")
            if new_xyz:
                top_fix_patch = {"kind": "MOVE_BASE", "new_xyz": new_xyz}

    now = datetime.now(timezone.utc).isoformat()

    store.insert(
        run_id=run_id,
        task_id=spec.task_id,
        created_at=now,
        verdict=packet.verdict.value,
        failed_gate=packet.failed_gate,
        top_fix=top_fix,
        evidence_path=str(run_evidence),
    )

    evidence_url = f"/runs/{run_id}/evidence"
    if PUBLIC_BASE_URL:
        evidence_url = f"{PUBLIC_BASE_URL}{evidence_url}"

    return JSONResponse(
        content={
            "run_id": run_id,
            "verdict": packet.verdict.value,
            "failed_gate": packet.failed_gate,
            "top_fix": top_fix,
            "top_fix_patch": top_fix_patch,
            "evidence_url": evidence_url,
            "evidence": packet.model_dump(mode="json"),
        },
        status_code=200,
    )


# ── API: list runs ───────────────────────────────────────────────────────


@app.get("/runs")
def list_runs(limit: int = 50) -> list[dict]:
    return store.list_recent(limit=limit)


# ── API: single run ──────────────────────────────────────────────────────


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    row = store.get(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


# ── API: evidence file ───────────────────────────────────────────────────


@app.get("/runs/{run_id}/evidence")
def get_evidence(run_id: str) -> FileResponse:
    row = store.get(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    path = Path(row["evidence_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Evidence file missing")
    return FileResponse(path, media_type="application/json")


# ── examples endpoints ────────────────────────────────────────────────────


@app.get("/examples")
def list_examples() -> list[str]:
    """Return sorted list of example YAML filenames."""
    if not EXAMPLES_DIR.is_dir():
        return []
    return sorted(p.name for p in EXAMPLES_DIR.glob("*.yaml"))


@app.get("/examples/{name}")
def get_example(name: str) -> PlainTextResponse:
    """Return raw YAML text for a bundled example."""
    # Guard against path traversal.
    if "/" in name or "\\" in name or name != Path(name).name:
        raise HTTPException(status_code=400, detail="Invalid example name")
    path = EXAMPLES_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Example not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


# ── AI endpoints ─────────────────────────────────────────────────────────


def _require_ai() -> None:
    if not ai.is_available():
        raise HTTPException(
            status_code=503,
            detail="GOOGLE_API_KEY is not set — AI features are unavailable.",
        )


@app.get("/ai/status")
def ai_status() -> dict:
    enabled = ai.is_available()
    result: dict = {"ai_enabled": enabled}
    if not enabled:
        result["reason"] = "GOOGLE_API_KEY is not set"
    return result


@app.post("/ai/generate")
async def ai_generate(request: Request) -> JSONResponse:
    _require_ai()
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    yaml_text = ai.generate_taskspec(prompt)
    return JSONResponse(content={"yaml": yaml_text})


@app.post("/ai/explain")
async def ai_explain(request: Request) -> JSONResponse:
    _require_ai()
    body = await request.json()
    evidence = body.get("evidence")
    if not evidence:
        raise HTTPException(status_code=400, detail="evidence is required")
    explanation = ai.explain_evidence(evidence)
    return JSONResponse(content={"explanation": explanation})
