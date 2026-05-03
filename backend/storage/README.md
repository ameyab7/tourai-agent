# Storage Module

Plan persistence (currently in-memory).

```
storage/
└── plan_store.py  # In-memory plan storage
```

**Key functions:**
- `plan_store.save(plan_id, snapshot)` — Save plan
- `plan_store.get(plan_id)` — Retrieve plan

**Note:** Currently in-memory. For production, replace with PostgreSQL or Redis.