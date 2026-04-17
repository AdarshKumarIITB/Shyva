"""Audit trail builder — records every classification step."""
from app.models.classification import AuditTrail


class AuditTrailBuilder:
    def __init__(self):
        self.trail = AuditTrail()

    def set_user_input(self, description: str, origin: str, destination: str):
        self.trail.user_input = description
        self.trail.add("input_received", f"Description: {description}, Lane: {origin}->{destination}")

    def set_effective_date(self, date: str):
        self.trail.effective_date = date
        self.trail.add("effective_date_set", f"Tariff lookup date: {date}")

    def set_normalized_facts(self, facts: dict):
        self.trail.normalized_facts = facts
        self.trail.add("facts_normalized", f"Extracted {sum(1 for v in facts.values() if v is not None)} known facts")

    def record_family_detection(self, family: str, reason: str):
        self.trail.add("family_detected", f"Routed to {family}: {reason}")

    def record_tree_decision(self, node_id: str, fact_key: str, value: str, legal_basis: str):
        self.trail.add(
            "tree_decision",
            f"Node {node_id}: {fact_key}={value} -> {legal_basis}",
        )

    def record_clarifying_question(self, fact_key: str, question: str):
        self.trail.add("clarification_needed", f"Missing {fact_key}: {question}")

    def record_code_considered(self, code: str, reason: str):
        self.trail.codes_considered.append(code)
        self.trail.add("code_considered", f"{code}: {reason}")

    def record_code_rejected(self, code: str, reason: str):
        self.trail.codes_rejected.append(code)
        self.trail.add("code_rejected", f"{code}: {reason}")

    def record_api_call(self, source: str, endpoint: str, result_summary: str):
        self.trail.api_calls.append({"source": source, "endpoint": endpoint, "result": result_summary})
        self.trail.add("api_verification", f"{source}: {endpoint} -> {result_summary}", source=source)

    def record_duty_layer(self, measure_type: str, rate: str, basis: str):
        self.trail.add("duty_layer", f"{measure_type}: {rate} ({basis})")

    def build(self) -> AuditTrail:
        return self.trail
