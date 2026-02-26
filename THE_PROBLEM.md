# The Missing Layer: Why AI-to-Robot Pipelines Fail and What It Takes to Fix Them

**February 2026**

---

## Abstract

Language models can now generate robot action plans from plain English. Vision-language-action models can map camera frames directly to motor commands. The AI side of robotics has advanced faster in the last two years than in the preceding two decades. Yet almost none of these systems are deployed in production. The reason is not capability — it is trust. There is no reliable way to verify that an AI-generated robot action is physically feasible before execution, and no systematic way to correct it when it is not. This paper examines the structural gap between AI-generated intent and physically valid robot behaviour, surveys the research landscape that defines the problem, and describes the technical requirements for a solution.

---

## 1. The Convergence

Two trends are colliding.

**AI models learned to talk to robots.** Google's SayCan (2022) demonstrated that large language models could be grounded in robotic affordances — combining what's *useful* (language understanding) with what's *possible* (learned skill feasibility) to select executable actions [1]. Stanford's Text2Motion (2023) added geometric feasibility checking, raising task success rates from 13% to 82% by ensuring plans were physically achievable before execution [2]. Google's Code as Policies (2022) showed that LLMs could write robot control programs directly as Python code, bypassing the action-primitive abstraction entirely [3]. DeepMind's Language to Rewards (2023) took a different approach, having LLMs define reward functions that get optimised into motor policies, achieving 90% task completion [4].

By 2025, vision-language-action (VLA) models — systems that fuse perception, language, and motor control into a single architecture — became the dominant research paradigm. NVIDIA's Groot N1 implements a dual-system design: a fast reactive module for 10ms motor control alongside a slow reasoning module for task planning and skill composition [5]. Green-VLA introduced staged training with out-of-distribution detection and safety alignment [6]. A comprehensive survey of VLA models across 2025-2026 identifies three converging architectural paradigms — early fusion, dual-system, and self-correcting — each addressing different aspects of grounding, generalisation, and action reliability [7].

**The robotics industry hit a programming bottleneck.** The global robot software market stands at USD 29.6 billion in 2026, projected to reach $78.8 billion by 2031 at 21.6% CAGR [8]. The AI robotics subset is growing even faster: $6.1 billion to $33.4 billion at 40.4% CAGR [9]. But growth is constrained by a structural limitation: programming robots still requires specialised engineers who understand kinematics, workspace geometry, and payload constraints. These people are expensive and scarce. The industry is sitting on deployed hardware it cannot fully utilise because the programming layer has not kept up.

ABB's launch of AppStudio in January 2025 — a no-code tool to create robotic interfaces, claiming 80% reduction in setup time — is a symptom of the bottleneck, not a solution to it [10]. It makes configuration easier but does not eliminate the expertise requirement. The fundamental problem remains: someone must understand what the robot can physically do before telling it what to do.

These two trends — AI that can generate robot instructions, and an industry that desperately needs easier robot programming — create the preconditions for a transformative shift. But there is a gap between them, and it is not a small one.

---

## 2. The Gap: AI Doesn't Know Physics

Every system listed above shares the same structural weakness. The AI component — whether LLM, VLA, or reward model — does not have reliable knowledge of the physical constraints governing a specific robot in a specific environment. It generates actions that are semantically reasonable but physically impossible.

### 2.1 What Goes Wrong

An LLM told to "pick up the box from the far shelf" will generate coordinates for the shelf. It has no way to know that the UR5e arm it's controlling only reaches 0.85 metres from its base, that the box weighs more than the arm's 5kg payload limit, or that the straight-line path to the shelf passes through a safety cage.

A VLA model processing camera input will output end-effector deltas that move toward the target. It has no explicit representation of joint limits, workspace boundaries, or forbidden regions. As Kim et al. (2026) document: "perception or grounding errors can immediately translate into unsafe motion; the policy may hallucinate object locations under visual ambiguity or out-of-distribution scenes" [11].

These failures manifest in three ways:

**Hardware faults.** The robot extends beyond its workspace, hits joint limits, triggers torque protection, or collides with the environment. Each failure costs downtime and risks damage. In collaborative settings, it raises safety concerns for nearby humans.

**Silent semantic errors.** The robot successfully executes an action that is physically valid but semantically wrong — it picks up the wrong object, places it in a valid but incorrect location, or follows a path that avoids obstacles but misses the goal. Physics validation catches the first failure mode; it cannot catch this one.

**Simulation escape.** Teams discover failures by running plans in simulation before hardware execution. This works but is expensive: simulation environments take weeks to build, require expertise to configure, and must be maintained as the physical environment changes. Most teams doing LLM-to-robot research do not have production-grade simulation — they are ML researchers, not simulation engineers.

### 2.2 The Grounding Problem

The research community has formalised this as the "grounding problem" — the gap between a model's language understanding and its knowledge of what is physically achievable [1][2][3].

SayCan's approach was to multiply language model scores ("is this action useful for the task?") by learned affordance scores ("can the robot actually do this?") [1]. This was foundational but limited: the affordance model is learned from experience, not computed from physics, so it can be wrong for novel situations. It also does not explain *why* an action is infeasible or *how* to fix it.

Text2Motion addressed this by adding a geometric feasibility planner that checks whether plans are physically achievable *before execution* [2]. The results were striking: methods with geometric feasibility planning "convincingly outperform the methods that do not" [2]. This was the first clear empirical evidence that pre-execution physics checking dramatically improves outcomes — a 6x improvement in success rate (82% vs 13%) over methods without it.

Code as Policies demonstrated the power of LLMs writing robot code directly [3], and Language to Rewards showed LLMs could define optimisable reward functions [4]. Both achieve impressive results but include no physics validation layer. The generated code can command physically impossible actions, and the only way to discover this is execution (or simulation).

### 2.3 Why Learned Grounding Is Insufficient

A natural response is: train the model to understand physics. Give it enough examples of feasible and infeasible actions, and it will learn to generate only feasible ones.

This approach has three fundamental problems:

**Distribution shift.** A model trained on one robot in one environment does not generalise to a different robot or a different environment. Joint limits, reach envelopes, payload capacities, and keepout zones are specific to each deployment. A model that learned UR5e physics will generate infeasible actions for a Franka Panda (different kinematics, different joint limits, different reach).

**Hallucination under ambiguity.** VLA models hallucinate object locations under visual ambiguity or out-of-distribution scenes [11]. When the model is uncertain about the scene, it generates actions based on its best guess — which may be physically infeasible. A deterministic physics check catches this regardless of the model's confidence.

**Certification.** For industrial deployment, "the model usually generates feasible actions" is not sufficient. ISO 10218:2025, the most significant robot safety standard revision in over a decade, now requires explicit functional safety documentation for every robot application — not just the robot, but the specific application it performs [12][13]. A learned model cannot provide deterministic guarantees. A physics computation can.

---

## 3. What the Research Says Is Needed

### 3.1 Modular Safety Guardrails

Kim et al. (2026) argue that foundation-model-enabled robots need **modular safety guardrails** — not end-to-end safety training, but separate, composable safety modules that can be verified independently [11]. They propose a two-layer architecture:

- **Monitoring and Evaluation Layer:** Assesses risk across the autonomy stack
- **Intervention Layer:** Enforces safety through decision-level gating and action-level filtering

They characterise robot safety along three dimensions:

1. **Action safety** — physical feasibility and constraint compliance
2. **Decision safety** — semantic and contextual appropriateness
3. **Human-centred safety** — conformance to human intent, norms, and expectations

The key insight is that these are separate concerns requiring separate solutions. Action safety (can the robot physically do this?) is a deterministic computation. Decision safety (should the robot do this?) requires reasoning. Human-centred safety (is this what the person wanted?) requires intent verification. Conflating them into a single model makes all three worse.

A companion paper, "Safety Guardrails for LLM-Enabled Robots" (2025), reinforces this finding: safety monitoring layers should "estimate action-level risk via token-level uncertainty from prediction entropy and cross-validate scene understanding with independent perception checks against workspace constraints to flag hallucinations" [14]. The phrase "independent perception checks against workspace constraints" describes exactly what a deterministic physics gate provides — an independent check that does not depend on the same model it is verifying.

### 3.2 Pre-Execution Verification

VerifyLLM (2025) proposes a formal framework for pre-execution verification of LLM-generated task plans [15]. The system converts natural language plans into Linear Temporal Logic (LTL) formulas and verifies them using LLM-based reasoning enhanced by the LTL formalism. This addresses *decision safety* — is the plan logically correct? — but does not address *action safety* — is the plan physically feasible?

T³ Planner (2025) takes a different approach: an LLM-enabled motion planning framework that self-corrects using formal methods to address hallucinations that could result in infeasible motion plans [16]. This is closer to physical validation but couples the planning and verification into a single LLM-based system, inheriting the reliability limitations of the underlying model.

Both systems validate the observation that pre-execution checking is necessary. Neither provides the deterministic, physics-based validation that industrial deployment requires.

### 3.3 The Fix Problem

The research literature has a blind spot. Nearly every paper cited above focuses on **detecting** infeasible actions. Almost none address what happens *after detection*.

When SayCan's affordance model scores an action as infeasible, the system selects a different action from its pre-defined skill library [1]. When Text2Motion's geometric planner rejects a configuration, the search algorithm backtracks [2]. When a VLA model's action is unsafe, the safety monitor intervenes — but the intervention is typically an emergency stop, not a correction [11].

None of these systems compute the *minimal change* needed to make a failed action feasible. None express that change in a format the AI model can consume for re-planning. None close the loop automatically.

This is the deeper problem. Detection without correction produces a system that can say "no" but cannot say "here's what to do instead." For an AI-to-robot pipeline to work reliably, the validation layer must not only detect infeasibility but produce structured, actionable corrections that feed back into the planning model.

---

## 4. What Needs to Be True

For AI-to-robot pipelines to move from research demonstrations to production deployment, five things must be true simultaneously:

### 4.1 Deterministic Physics Validation

Every proposed action must be checked against physical constraints — kinematics (can the robot's joints reach this pose?), workspace geometry (is the target within reach?), payload limits (can the robot lift this mass?), and spatial constraints (does the path avoid forbidden regions?).

This validation must be deterministic: same input, same output, every time. Stochastic or learned validation introduces the same unpredictability it is supposed to prevent. A physics gate computed from the robot's URDF and the environment's geometry provides the necessary guarantee.

Text2Motion's results provide the clearest evidence: geometric feasibility planning raises success rates from 13% to 82% [2]. The improvement is not marginal — it is the difference between a research demo and a working system.

### 4.2 Structured Counterfactual Fixes

When validation fails, the system must compute the minimal change that would make the action feasible. This fix must be structured — not just "try again" but "move the target to [0.75, 0.57, 0.21]." It must be expressed in both human-readable language (so an LLM can consume it) and machine-readable coordinates (so a deterministic planner can use it).

The fix computation is the hard part. Detecting that a target is out of reach is trivial (compare distance to max_reach). Computing the nearest reachable point via forward kinematics of the best IK solution, in the robot's specific kinematic configuration, with the correct joint limits — that requires the physics engine to work *backwards* from the failure to the correction.

### 4.3 Closed-Loop Feedback

The fix must flow back to the action source automatically. If the source is an LLM, the fix instruction is appended to the prompt as a constraint. If the source is a VLA model, the fix coordinates are fed back as a waypoint correction. If the source is a human operator, the fix is displayed as a suggestion.

The loop must terminate in bounded time: either the plan converges to a feasible solution, or the system reports that no feasible solution exists within the retry budget. Unbounded retry loops are unacceptable for production systems.

### 4.4 Robot-Specific Knowledge

The validation layer must know the specific robot's capabilities: its kinematic chain (from a URDF), its joint limits, its maximum reach, its payload capacity. Generic "a robot arm can reach about a metre" is not sufficient. A Franka Panda has 7 DOF with a 0.855m reach and asymmetric joint limits (joint 4 is constrained to [-3.07, -0.07] radians). A KUKA iiwa 14 has 7 DOF with a 0.82m reach and 14kg payload. A UR5e has 6 DOF with a 1.85m reach and 5kg payload. The validation must be parametric in the robot.

This is why bundled robot profiles with kinematic URDFs matter. If the user must provide their own URDF, configure link names, and set joint limits manually, the barrier to adoption is too high for non-experts — and the whole point of the AI-to-robot pipeline is to serve non-experts.

### 4.5 Environment-Specific Constraints

Physical feasibility depends on the environment, not just the robot. Keepout zones (safety cages, conveyor housings, other equipment), workspace boundaries, and obstacle locations are deployment-specific. The validation layer must accept environment descriptions and check actions against them.

ISO 10218:2025 makes this explicit: collaborative robots must be evaluated based on the *application* — the combination of robot + task + environment — not the robot alone [12]. The safety standard now requires assessing "the entire environment within which the robot operates." A validation layer that only checks the robot's kinematic limits, without considering the workspace, does not meet the standard.

---

## 5. The State of the Field

### 5.1 What Exists

**Full simulation environments** (NVIDIA Isaac Sim, Gazebo, PyBullet, MuJoCo) provide comprehensive physics validation but require significant setup time, domain expertise, and computational resources. NVIDIA's Isaac Sim now supports hardware-in-the-loop validation with digital twins [17], and companies like Cyngn are using it to validate autonomous vehicle policies in persistent simulated environments [18]. However, simulation is a development tool, not a runtime check — it validates plans offline, not at the speed of LLM generation.

**Motion planning libraries** (MoveIt, OMPL) solve for collision-free paths but do not generate structured fixes when planning fails. They report "planning failed" — they do not report "the target is 0.62m beyond the robot's reach; the nearest reachable point is [0.75, 0.57, 0.21]."

**Learned affordance models** (SayCan, RT-2) score action feasibility but are specific to the robots and environments they were trained on. They do not generalise to new robots without retraining, and they do not provide deterministic guarantees.

**Formal verification systems** (VerifyLLM, T³ Planner) verify logical plan structure but not physical feasibility. They can confirm that a plan's steps are in the right order but not that each step is kinematically achievable.

### 5.2 What's Missing

No existing system provides all five requirements simultaneously:

| System | Deterministic | Structured Fixes | Closed Loop | Robot-Specific | Environment-Aware |
|--------|:---:|:---:|:---:|:---:|:---:|
| Isaac Sim / PyBullet | Yes | No | No | Yes | Yes |
| MoveIt / OMPL | Yes | No | No | Yes | Yes |
| SayCan | No | No | No | Partially | Partially |
| Text2Motion | Partially | No | Partially | Yes | Yes |
| VerifyLLM | No | No | No | No | No |
| Code as Policies | No | No | No | No | No |

The gap is in the combination. Individual pieces exist. The integrated solution — deterministic physics validation with structured fixes that close the loop automatically, parametric in the robot and environment — does not.

---

## 6. Why Now

Three forces make this problem urgent in 2026:

### 6.1 VLA Deployment Is Accelerating

The VLA field is moving from research to deployment. NVIDIA's partnerships with Agility Robotics, Boston Dynamics, Figure AI, Franka Robotics, and others are bringing foundation-model-enabled robots to production environments [17]. As more teams deploy VLAs, the need for a deterministic safety layer between the model's output and the robot's actuators grows proportionally. More AI in the loop means more need for physics verification.

### 6.2 Regulatory Pressure Is Building

ISO 10218:2025 introduced explicit functional safety requirements that were previously implied [12][13]. The standard now requires:
- Risk-based evaluation of collaborative applications
- Explicit documentation of safety functions
- Structured lifecycle coverage
- Cybersecurity requirements

An EvidencePacket — a timestamped, structured record of which physical constraints were checked, what was measured, and whether the action passed or failed — is precisely the documentation artifact that compliance requires.

### 6.3 The Integration Bottleneck Is Quantified

The robot software market data makes the bottleneck measurable [8][9]. Growth is constrained by a skilled workforce requirement: "developing and maintaining robot software requires a skilled workforce with expertise in robotics, software development, and AI" [8]. The market is telling us that the programming barrier is the binding constraint on robotics adoption. AI-to-robot pipelines can remove that barrier — but only if the physics validation layer exists.

---

## 7. Requirements for a Solution

Based on the research landscape and the five conditions outlined in Section 4, a viable solution must:

1. **Validate deterministically.** Given a robot (URDF + joint limits + reach + payload), an environment (keepout zones + safety buffer), and a proposed action (target pose + object mass), compute a definitive pass/fail verdict. No randomness, no learned heuristics, no "probably fine."

2. **Compute structured fixes.** When an action fails, produce the minimal change — in exact coordinates and in natural language — that would make it feasible. Express fixes in a format that both LLMs (language) and code (coordinates) can consume.

3. **Close the loop.** Feed fixes back to the action source automatically. Bound the retry budget. Provide full observability into every attempt (what was proposed, what failed, what fix was generated, what was proposed next).

4. **Be parametric in the robot.** Support multiple robots from a registry of profiles with bundled kinematic models. The user selects a robot by name; the system loads the correct URDF, joint limits, reach envelope, and payload capacity automatically.

5. **Be parametric in the environment.** Accept environment descriptions (keepout zones, safety buffers, workspace boundaries) and validate actions — including motion paths, not just endpoints — against them.

6. **Require no simulation.** Run in milliseconds on a CPU. No GPU, no physics simulator, no digital twin. The validation must be fast enough to run in the LLM's generation loop — not as a separate offline step.

7. **Produce audit artifacts.** Every validation produces a structured evidence record with measured values, reason codes, and timestamps. This record serves as the compliance documentation that ISO 10218:2025 requires.

8. **Integrate at multiple levels.** Serve developers (Python SDK), CI/CD pipelines (CLI with JUnit output), and teams (REST API with web UI). The same physics engine, accessed through interfaces appropriate to each user.

---

## 8. Conclusion

The AI side of robotics has crossed the capability threshold. LLMs can generate robot programs from English. VLAs can map perception to action. The models work. What does not work is the connection between what the model proposes and what the robot can physically do.

This is not a training problem — it is an architecture problem. The solution is not better models but a separate, deterministic physics layer that validates every action before execution and produces structured corrections when validation fails. The research community has identified this need [2][11][14][15]. The standards bodies have formalised the compliance requirements [12][13]. The market has quantified the bottleneck [8][9].

The missing layer is not the AI. The missing layer is the physics.

---

## References

[1] Ahn, M. et al. "Do As I Can, Not As I Say: Grounding Language in Robotic Affordances." arXiv:2204.01691, 2022. https://arxiv.org/abs/2204.01691

[2] Lin, K. et al. "Text2Motion: From Natural Language Instructions to Feasible Plans." arXiv:2303.12153, 2023. https://arxiv.org/abs/2303.12153

[3] Liang, J. et al. "Code as Policies: Language Model Programs for Embodied Control." arXiv:2209.07753, 2022. https://arxiv.org/abs/2209.07753

[4] Yu, W. et al. "Language to Rewards for Robotic Skill Synthesis." arXiv:2306.08647, 2023. https://arxiv.org/abs/2306.08647

[5] NVIDIA. "NVIDIA Releases New Physical AI Models as Global Partners Unveil Next-Generation Robots." NVIDIA Newsroom, 2025. https://nvidianews.nvidia.com/news/nvidia-releases-new-physical-ai-models-as-global-partners-unveil-next-generation-robots

[6] "Green-VLA: Staged Vision-Language-Action Model for Generalist Robots." arXiv:2602.00919, 2026. https://huggingface.co/papers/2602.00919

[7] "Vision-Language-Action Models: Concepts, Progress, Applications and Challenges." arXiv:2505.04769, 2025. https://arxiv.org/html/2505.04769v1

[8] Mordor Intelligence. "Robot Software Market — Size, Share & Industry Forecast." 2026. https://www.mordorintelligence.com/industry-reports/robot-software-market

[9] MarketsandMarkets. "Artificial Intelligence Robots Market Size, Share, Industry Growth, Trends & Analysis, 2030." 2025. https://www.marketsandmarkets.com/Market-Reports/artificial-intelligence-robots-market-120550497.html

[10] ABB AppStudio announcement, January 2025, cited via GMInsights Robotic Software Market report. https://www.gminsights.com/industry-analysis/robotic-software-market

[11] Kim, J. et al. "Modular Safety Guardrails Are Necessary for Foundation-Model-Enabled Robots in the Real World." arXiv:2602.04056, 2026. https://arxiv.org/abs/2602.04056

[12] ANSI Blog. "ISO 10218-1:2025 — Robots And Robotic Devices Safety." 2025. https://blog.ansi.org/ansi/iso-10218-1-2025-robots-and-robotic-devices-safety/

[13] Robotics 24/7. "A3 revises ISO 10218 robot safety standards for 2025." 2025. https://www.robotics247.com/article/a3_revises_iso_10218_robot_safety_standards_for_2025

[14] "Safety Guardrails for LLM-Enabled Robots." arXiv:2503.07885, 2025. https://arxiv.org/abs/2503.07885

[15] "VerifyLLM: LLM-Based Pre-Execution Task Plan Verification for Robots." arXiv:2507.05118, 2025. https://arxiv.org/abs/2507.05118

[16] "T³ Planner: A Self-Correcting LLM Framework for Robotic Motion Planning with Temporal Logic." arXiv:2510.16767, 2025. https://arxiv.org/html/2510.16767v1

[17] NVIDIA. "NVIDIA Accelerates Robotics Research and Development With New Open Models and Simulation Libraries." NVIDIA Newsroom, 2025. https://nvidianews.nvidia.com/news/nvidia-accelerates-robotics-research-and-development-with-new-open-models-and-simulation-libraries

[18] Cyngn. "Cyngn Advances Physical AI by Testing Autonomous Vehicle Software Using NVIDIA Isaac Sim." 2026. https://www.automation.com/article/cyngn-physical-ai-testing-autonomous-vehicle-software-nvidia-isaac-sim

[19] Reynolds-Moore. "2026 State of Practice from a Functional Safety Engineer." 2025. https://reynolds-moore.com/2025/12/31/2026-state-of-practice-from-a-functional-safety-engineer/

[20] Moritz Reuss. "State of Vision-Language-Action (VLA) Research at ICLR 2026." 2026. https://mbreuss.github.io/blog_post_iclr_26_vla.html

[21] GT-RIPL. "Awesome-LLM-Robotics: A comprehensive list of papers using large language/multi-modal models for Robotics/RL." GitHub. https://github.com/GT-RIPL/Awesome-LLM-Robotics
