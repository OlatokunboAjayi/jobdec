"""
Specialized cognitive domain solvers for the Autonomous Assessment Solver (R3):
- BehavioralSolver (STAR essays, SJT rankings, Likert scale consistency)
- TechnicalSolver (Algorithmic coding, subprocess test verification, technical MCQs)
- MathematicsSolver (Neuro-symbolic SymPy exact calculation engine)
- LogicSolver (Sequence difference detector, syllogism reasoner, linear ordering, matrix logic)
"""

from assessments.domains.behavioral import BehavioralSolver
from assessments.domains.technical import TechnicalSolver
from assessments.domains.mathematics import MathematicsSolver
from assessments.domains.logic import LogicSolver

__all__ = [
    "BehavioralSolver",
    "TechnicalSolver",
    "MathematicsSolver",
    "LogicSolver",
]
