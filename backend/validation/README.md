# Validation Module

Final plan assembly and validation.

```
validation/
└── validator.py  # Merges skeleton + narration into FinalPlan
```

**Key functions:**
- `assemble_and_validate(...)` — Main entry point, merges skeleton + narration
- `_merge_day(...)` — Merges day skeleton with LLM narration

**Validates:**
- All scheduled stops have narration
- Day labels and rain plans present
- Weather data attached
- Trip-level metadata complete