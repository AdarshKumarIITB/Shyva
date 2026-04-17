"""Domain models for the persisted classification dossier state machine."""

from app.domain.dossier import ClassificationDossier
from app.domain.state_machine import WorkflowState

__all__ = ["ClassificationDossier", "WorkflowState"]
