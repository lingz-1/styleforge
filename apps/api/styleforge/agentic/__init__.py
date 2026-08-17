"""Agentic loop (Stage 2 minimal): Semantic Agent + Deterministic Environment.

The three-layer boundary in code:
  - ``agent.py``      Semantic Agent — understands the user, decides, replans.
  - ``environment.py`` Deterministic Environment — facts, execution, legality.
  - ``reviewer.py``   Semantic Reviewer — intent fidelity + quality gate.
  - ``structure.py``  the physical ontology (region × layer × occupancy).
  - ``shadow.py``     ShadowRunner — runs alongside legacy, never commits.
"""
