"""Compatibility layer for the new dossier-backed classification workflow."""
from __future__ import annotations

from app.domain.dossier import ClassificationDossier
from app.engine.classification_workflow import continue_classification, start_classification

__all__ = ["ClassificationDossier", "start_classification", "continue_classification"]
