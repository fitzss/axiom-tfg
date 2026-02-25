# Axiom: The Physics Layer for the AI-Robotics Stack

---

## The moment

Two things became true at the same time.

First, LLMs got good enough to generate robot programs from plain English. Google demonstrated it with Code as Policies. Microsoft demonstrated it with ChatGPT for Robotics. Every major robotics lab is now experimenting with language-to-robot pipelines. The ability to say "pick up the mug and put it on the shelf" and get a robot program is no longer theoretical — it works, today, with off-the-shelf models.

Second, the robotics industry hit an integration bottleneck. There are more robots deployed than ever, but programming them still requires specialised engineers who understand kinematics, workspace geometry, and payload constraints. These people are expensive and scarce. The industry is sitting on installed hardware it can't fully utilise because the programming layer hasn't kept up.

These two facts create an opening. LLMs can bridge the gap between what a non-expert wants and what a robot needs to hear. But there's a missing piece — and it's the reason nobody has shipped a reliable language-to-robot product yet.

## The problem

**LLMs don't know physics.**

An LLM can understand "pick up the heavy box from the far shelf." It can generate `robot.pick(x=2.5, y=1.0, z=2.0)`. What it cannot know is that this particular robot arm only reaches 0.85 metres from its base, that the box weighs more than the arm can lift, or that the path crosses through a safety cage.

Today, these failures are discovered in one of three ways:

**On the robot.** The arm extends fully, can't reach, and faults. Or it picks up something too heavy and triggers a torque limit. This costs downtime, risks hardware damage, and in collaborative settings, raises safety concerns. Every failed attempt is time and money.

**In simulation.** The team sets up a digital twin, runs the plan, watches it fail. This catches the problem before hardware, but simulation environments take weeks to build, require expertise to configure, and are expensive to maintain. Most teams doing LLM-to-robot experiments don't have a sim environment — they're ML researchers, not simulation engineers.

**By a human reviewer.** Someone who knows the robot looks at the generated code and spots the problem. This works but doesn't scale. The whole point of using an LLM was to remove the human from the programming loop.

None of these are acceptable for production deployment. And none of them tell the LLM what to do differently.

## The deeper problem

The real issue isn't detecting failures. MoveIt can tell you IK failed. PyBullet can show you a collision. The real issue is **what happens after the failure**.

When a current system says "IK failed," the LLM doesn't know what to do with that. It doesn't know what IK is. It doesn't know which direction to move the target, by how much, or what the robot can actually reach. So either a human intervenes to fix the plan, or the system gives up.

What's missing is a layer that:

1. Validates the plan deterministically — not "probably fine" but "definitely yes or definitely no"
2. When the answer is no, computes the **smallest change that would make it yes**
3. Expresses that change in natural language the LLM can understand
4. Feeds it back automatically so the LLM can try again

This is what Axiom does. It's not a validator. It's a **physics compiler** — it takes intent that may be physically impossible and iteratively compiles it into a plan that is physically guaranteed to work.

## How it works

```
Non-expert: "Pick up the box from the far shelf and put it by the door"

LLM generates: pick at [2.5, 1.0, 2.0], place at [3.0, 0.0, 0.5]

Axiom: pick target is 2.5m away — robot reaches 0.85m
       Fix: "Move target within 0.85m. Nearest reachable: [0.75, 0.57, 0.21]"

LLM regenerates with constraint: pick at [0.75, 0.57, 0.21]

Axiom: place target is 3.0m away
       Fix: "Nearest reachable: [0.82, 0.0, 0.27]"

LLM regenerates: place at [0.82, 0.0, 0.27]

Axiom: all checks pass — execute

Total time: ~4 seconds. Zero human intervention.
```

The LLM handles the language. Axiom handles the physics. The fix loop closes naturally because both sides speak language — the LLM reads the fix instruction and adjusts.

## Who needs this

### Today: teams deploying LLM-to-robot pipelines

These are robotics companies and research labs experimenting with language-driven robot programming. They've seen the demos. They know LLMs can generate robot code. But they can't ship it because the generated code fails unpredictably on real hardware. They need a reliability layer.

These teams have robots, they have LLM access, and they have a use case. What they don't have is a way to make the LLM's output physically trustworthy. Axiom is the missing piece between their prototype and a deployable system.

### Tomorrow: robot OEMs and platform companies

Universal Robots, FANUC, ABB, and Yaskawa all face the same problem: their customers can't program their robots easily enough. These OEMs are actively exploring AI-assisted programming. Axiom becomes the physics middleware they embed in their platforms — the layer that ensures whatever the AI generates is valid for their specific hardware.

This is an integration sale, not a replacement. Axiom doesn't compete with the OEM's software stack — it sits inside it, making their AI features reliable.

### The unlock: non-experts who have robots but not robotics engineers

Small and mid-sized manufacturers. Logistics companies. Research labs. Agriculture operations. They bought robots because someone sold them on automation. But programming those robots requires expertise they don't have and can't easily hire. The robot sits underutilised or runs the same fixed program it was initially set up with.

If LLM-to-robot works reliably — and Axiom is the layer that makes it reliable — these organisations can reprogram their robots by describing tasks in English. No integrator visit. No teach pendant. No offline programming software. Describe the task, the system generates a valid program, the robot executes.

This is where the market gets large. There are millions of industrial robots deployed worldwide, and the bottleneck on utilisation is programming, not hardware.

## Why now

**LLMs crossed the capability threshold.** Two years ago, LLM-generated robot code was a research curiosity. Today, GPT-4o-mini can reliably generate pick-and-place action sequences from natural language descriptions. The frontend of the compiler exists. The backend (robot hardware) has existed for decades. The middle — the physics validation and fix layer — is what's missing.

**The robotics industry is actively looking for this.** Every major robot OEM has announced AI-assisted programming initiatives. Universal Robots has PolyScope X. FANUC has CRX integration. ABB is investing in AI path planning. They all need a physics grounding layer for whatever AI they plug in. Nobody has built it as a standalone, embeddable component.

**VLAs are creating more demand, not less.** Vision-Language-Action models are the hottest area in robotics AI. As more teams deploy VLAs and LLM-based planners, the need for a deterministic safety layer between the AI's output and the robot's actuators grows proportionally. More AI in the loop means more need for physics verification.

## What makes Axiom defensible

**The fix engine is the moat.** Telling someone that IK failed is commodity — any robotics library can do that. Telling them exactly what to change, in coordinates AND in natural language, and feeding it back to an LLM automatically — nobody does that. The counterfactual fix computation is the core IP, and it deepens with every new gate, every new robot model, and every new constraint type.

**Data network effects from the loop.** Every resolve cycle generates a (prompt, failed plan, fix, successful plan) tuple. This data is valuable for fine-tuning LLMs specifically for robot code generation. The more people use the system, the better the LLM gets at generating valid plans on the first try — which makes the system faster and more reliable, which brings more users.

**Embeddable, not competitive.** Axiom doesn't compete with robot OEMs, simulation platforms, or LLM providers. It sits between them, making them work together. This makes it a natural integration partner rather than a competitive threat. The same physics layer works whether the LLM is GPT, Claude, Llama, or a custom model — and whether the robot is a UR5, a Franka, or a FANUC.

**Determinism as trust.** The entire gate pipeline is deterministic — same input, same output, every time. This matters for safety certification, regulatory compliance, and customer trust. A learned/heuristic approach to validation introduces the same unpredictability it's supposed to prevent. Axiom's gates are computed from physics, not learned from data.

## What we think matters most

Three things matter more than anything else for this to work:

**The fix, not the gate, is the product.** Anyone can write a reach check. The unique value is the structured, actionable fix that closes the loop with the LLM. Every engineering decision should be evaluated against: does this produce better fixes? Does this make the loop converge faster?

**Developer experience is the wedge.** The first adopters are developers building LLM-to-robot pipelines. They need `pip install axiom-tfg` and three lines of Python. Not a platform. Not a dashboard. Not a sales call. A library that works out of the box with their existing stack. The three-line integration is what gets them in the door. Everything else — CLI, API, web UI — is secondary.

**Trust is earned robot by robot.** Nobody will trust this with a million-dollar production line on day one. Trust comes from proving reliability on one robot, then two, then a fleet. The path is: developer experiments with it → it saves them time → they integrate it → their company depends on it → they can't remove it. This is an infrastructure adoption pattern, not a sales motion.

## What could be enabled

If the physics layer works — if anyone can go from English to validated robot program — several things become possible that aren't today:

**Robotics without roboticists.** A warehouse manager describes a new packing pattern. A lab technician describes a sample preparation protocol. A small manufacturer describes a new assembly step. The robot reprograms itself. The expertise bottleneck disappears.

**Rapid task switching.** Today, changing what a robot does requires an integrator visit or an engineer with a teach pendant. With language-to-robot, switching tasks takes minutes, not weeks. This makes robots economical for small-batch, high-mix operations — the fastest-growing segment of manufacturing.

**AI-native robot fleets.** When programming is language-based and validation is automatic, scaling from one robot to a fleet becomes a software deployment problem, not an integration problem. Describe the task once, validate it against each robot's specific capabilities, deploy to the fleet.

**A new application layer.** Just as app stores emerged when phones became programmable by non-engineers, a new layer of robotics applications becomes possible when robots become programmable by non-engineers. Task templates, sharable programs, marketplace dynamics — all enabled by removing the expertise barrier.

---

*Axiom is open-source (MIT), implemented in Python, and working today. The core system — gate pipeline, fix engine, resolve loop, and LLM codegen adapter — is built and tested (236 tests). End-to-end demonstration with real LLMs has been validated.*
