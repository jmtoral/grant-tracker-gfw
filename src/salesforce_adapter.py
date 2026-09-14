"""The ONLY module that knows Salesforce's shape. Builds a payload; never calls the network."""
from src.models import ApprovedGrantApplication


class SalesforceAdapter:
    def __init__(self, mapping: dict):
        self.mapping = mapping  # config/salesforce_mapping.yaml

    def to_salesforce(self, approved: ApprovedGrantApplication) -> dict:
        payload, warnings = {}, []
        for field, value in approved.model_dump().items():
            m = self.mapping.get(field)
            if m is None or value is None or value == []:
                continue
            if "value_map" in m:
                values = value if isinstance(value, list) else [value]
                missing = [v for v in values if v not in m["value_map"]]
                if missing:
                    warnings.append(f"{field}: no value_map entry for {missing}, sent unchanged")
                mapped = [m["value_map"].get(v, v) for v in values]
                value = mapped if isinstance(value, list) else mapped[0]
            if isinstance(value, list):  # multipicklist
                value = ";".join(value)
            if isinstance(value, str) and len(value) > m.get("max_length", len(value)):
                warnings.append(f"{field}: truncated to {m['max_length']} characters")
                value = value[:m["max_length"]]
            payload.setdefault(m["object"], {})[m["field"]] = value
        payload["_warnings"] = warnings
        return payload
