---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Ask the whole frontier in one round, then wait for the user's answers before the next round.

Present every round with the modal `question` tool, never as a plain-text or Markdown questionnaire. Put each frontier decision in the tool's questions list; use a concise title, put context and tradeoffs in the question body, and offer clear options when appropriate. Put your recommended option first and mark it `(Recommended)`. Word the question so choosing that option accepts your recommendation. For questions that need an unconstrained answer, rely on the tool's free-form option rather than printing a text prompt. Group independent frontier decisions into the same modal call, and do not include decisions whose prerequisites are still unanswered.

Do not continue the design tree until the user has answered the modal. If the modal question tool is unavailable, follow the applicable global instructions for that situation.

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Finding _facts_ is your job, never the user's. When a frontier question needs a fact from the environment (filesystem, tools, etc.), dispatch a sub-agent to find it; don't ask the user for anything you could look up yourself. Don't block on it: a running exploration is an unsettled prerequisite, so only the questions downstream of it wait for the sub-agent to report; ask the rest of the frontier now. The _decisions_ are the user's: put each to them and wait.

The session is done when the frontier is empty: every branch of the design tree visited, nothing left silently assumed. Do not act on it until the user confirms you have reached a shared understanding.
