# K Recruit — Production Readiness Backlog

Last updated: 2026-05-26
Owner: Sushrut Nistane

---

## Status Legend
- 🔴 Blocked — waiting on external team / approval
- 🟡 Pending — can start, no blocker
- 🟢 Done

---

## P0 — Go-Live Blockers (must complete before production)

### 1. 🟢 Data Isolation — uploaded_by on candidates table
**What:** Add `uploaded_by` foreign key (user_id) to the candidates table so each recruiter only sees their own uploaded candidates. Currently all recruiters see all candidates.
**Why:** Without this, Recruiter A can see Recruiter B's candidates — unacceptable for 100-user rollout.
**Work:**
- Add `uploaded_by` column to candidates table in `models/candidate.py`
- Add to `run_migrations()` in `database.py`
- Filter `GET /candidates/` by `uploaded_by = current_user.id` for recruiter role
- Admins/super_admins can still see all candidates
- Update `to_dict()` to include `uploaded_by`

---

### 2. 🔴 PostgreSQL Migration
**What:** Replace SQLite (single file, single user) with PostgreSQL (multi-user, concurrent).
**Why:** SQLite cannot handle 100 concurrent recruiters — writes are serialized, file locks cause errors.
**Blocked by:** IT provisioning a database server or approving a cloud VM.
**Work when ready:**
- Set `DATABASE_URL=postgresql://user:pass@host/dbname` in `.env`
- Run `pip install psycopg2-binary`
- Restart server — SQLAlchemy auto-creates all tables on PostgreSQL
- Migrate existing SQLite data using `pg_loader` or a one-time script

---

### 3. 🔴 Microsoft Entra ID (SSO Login)
**What:** "Sign in with Microsoft" button on the login page using Kforce employee accounts.
**Why:** 100 recruiters should not manage separate K Recruit passwords. Use existing Kforce AD credentials.
**Blocked by:** IT providing Azure App Registration credentials.
**IT action needed:**
1. Azure Portal → App Registrations → New registration → Name: "K Recruit"
2. Supported account types: Single tenant
3. Authentication tab → Add platform: Single-page application (SPA)
4. Add redirect URIs: `http://localhost:8000`, production server IP
5. Enable: ID tokens (under Implicit grant)
6. Share: Application (client) ID and Directory (tenant) ID
**Work when IT provides IDs:**
- Set `ENTRA_CLIENT_ID=<value>` in `.env`
- Set `ENTRA_TENANT_ID=<value>` in `.env`
- Restart server — Microsoft button appears automatically
- Update each user's email in Users page to their Kforce email (firstname.lastname@kforce.co.in)

---

### 4. 🟡 Admin Account Email Update
**What:** Change the default admin account email from `admin` to `sushrut.nistane@kforce.co.in`.
**Why:** When Entra ID is configured, login matching is done by email — admin account must match Kforce email.
**Work (manual, no code change):**
- Log in to K Recruit
- Go to Users page → edit admin account
- Change email to `sushrut.nistane@kforce.co.in`
- Save

---

## P1 — Network & Security (required before office-wide rollout)

### 5. 🔴 Office LAN / Network Access
**What:** Allow office staff to reach K Recruit from their laptops on office WiFi/LAN.
**Why:** Currently only accessible from the server laptop's own browser.
**Blocked by:** IT firewall rule approval.
**IT action needed:**
- Allow inbound TCP on port 443 from office subnet to server laptop IP
- Or configure internal DNS: `krecruit.kforce.local` → server IP
**Two phases:**
- Phase 1: Office LAN / WiFi (firewall rule on office subnet)
- Phase 2: Kforce VPN (for remote/home access — same firewall rule, VPN handles routing)

---

### 6. 🔴 nginx + SSL Certificate
**What:** Put nginx as a reverse proxy in front of the FastAPI server with HTTPS.
**Why:** Browsers block mixed content; HTTPS required for Microsoft MSAL login popup to work; security best practice.
**Blocked by:** IT providing SSL certificate or approving Let's Encrypt.
**Work when ready:**
- Install nginx on server laptop
- Configure reverse proxy: `https://server-ip` → `http://localhost:8000`
- Install SSL certificate (Let's Encrypt or IT-provided)
- Update `CORS_ORIGINS` in `.env` to include the HTTPS origin
- Update Entra ID redirect URIs in Azure to the HTTPS URL
- Set `APP_ENV=production` in `.env` to enable HSTS header

---

## P2 — Production AI (replace local Ollama with Azure)

### 7. 🔴 Azure OpenAI Provisioning
**What:** Replace local Ollama model with Azure OpenAI (GPT-4o) for resume analysis and rewriting.
**Why:** Ollama runs a small local model (qwen2.5:0.5b) — quality is limited. GPT-4o produces significantly better analysis and rewriting. No data leaves Kforce's Azure tenant.
**Blocked by:** IT/Azure team provisioning the Azure OpenAI resource.
**IT action needed:**
- Azure Portal → Azure OpenAI → Create resource
- Deploy two models: `gpt-4o` (analysis/rewriting) and `text-embedding-3-small` (semantic scoring)
- Share: endpoint URL and API key
**Work when ready:**
- Go to K Recruit Settings page → change provider to "azure"
- Enter endpoint URL, API key, deployment name
- Click "Test Connection" to verify
- No code change needed — provider switching is built in

---

### 8. 🔴 Azure AI Search (Vector Database)
**What:** Replace in-memory semantic scoring with Azure AI Search vector index.
**Why:** Current semantic scoring uses a local 22 MB model in RAM. Azure AI Search provides proper vector storage, scales to thousands of resumes, and persists embeddings across restarts.
**Blocked by:** IT/Azure team provisioning Azure AI Search resource.
**Work when ready:**
- Update `services/semantic_service.py` to use Azure AI Search SDK
- Store resume embeddings in Azure AI Search index on upload
- Query index for similarity on analysis/alignment runs
- Remove local sentence-transformers dependency

---

### 9. 🟡 Update semantic_service.py for Azure Embeddings
**What:** Replace HuggingFace `all-MiniLM-L6-v2` with Azure `text-embedding-3-small`.
**Why:** No local model dependency, consistent with production AI stack, no HF_HUB_OFFLINE workaround needed.
**Depends on:** Item 7 (Azure OpenAI provisioned).
**Work:**
- Add Azure embedding call in `semantic_service.py`
- Use `text-embedding-3-small` deployment via Azure OpenAI SDK
- Remove `sentence-transformers` and `HF_HUB_OFFLINE` from `.env`

---

## P3 — Frontend & Compliance

### 10. 🟡 Vite Build Pipeline (replace CDN React)
**What:** Bundle all JavaScript dependencies locally using Vite instead of loading React/Babel from CDN at runtime.
**Why:** CDN calls in production are a compliance risk (data flows to CDN provider). Vite produces a self-contained bundle — zero CDN calls at runtime, enables strict Content Security Policy (CSP).
**Work:**
- Install Node.js + Vite
- Move frontend JSX from `index.html` / `preview.html` into proper `.jsx` files
- Run `vite build` → produces `/dist` static bundle
- Update FastAPI to serve `/dist` instead of `/static`
- Add strict CSP header in `main.py` (currently deferred because Babel needs `unsafe-eval`)

---

## P4 — Operational (post go-live)

### 11. 🟡 Audit Log Monitoring
**What:** Set up regular review of `backend/audit.log`.
**Why:** Every API call is already logged (IP, user ID, method, path, status, response time). IT security team should review this log periodically.
**Work:** Configure log rotation (e.g. logrotate on Linux, Task Scheduler on Windows) so the file does not grow indefinitely. Share log location with IT security team.

---

### 12. 🟡 JWT Secret Rotation for Production
**What:** Generate a new `JWT_SECRET` for the production server.
**Why:** Current secret in `.env` has been visible to developers during setup. Best practice is to generate a fresh secret on the production server that no one has seen.
**Work:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
Paste output into production `.env` as `JWT_SECRET=`. All active sessions will be invalidated (users re-login once).

---

### 13. 🟡 Session Token Expiry Review
**What:** Review JWT token lifetime (currently 24 hours).
**Why:** 24 hours covers a full workday. For higher security, reduce to 8 hours (one shift). Users are re-prompted to log in the next day.
**Work:** Change `timedelta(hours=24)` to `timedelta(hours=8)` in `backend/routers/auth.py:49`.

---

## Summary Table

| # | Item | Priority | Status | Owner |
|---|---|---|---|---|
| 1 | Data isolation (uploaded_by) | P0 | 🟢 Done | Dev |
| 2 | PostgreSQL migration | P0 | 🔴 Blocked (IT) | IT + Dev |
| 3 | Entra ID SSO login | P0 | 🔴 Blocked (IT) | IT + Dev |
| 4 | Admin email update | P0 | 🟡 Pending | Sushrut |
| 5 | Office LAN / network access | P1 | 🔴 Blocked (IT) | IT |
| 6 | nginx + SSL certificate | P1 | 🔴 Blocked (IT) | IT + Dev |
| 7 | Azure OpenAI provisioning | P2 | 🔴 Blocked (IT/Azure) | IT + Dev |
| 8 | Azure AI Search | P2 | 🔴 Blocked (IT/Azure) | IT + Dev |
| 9 | Azure Embeddings in code | P2 | 🟡 Pending (needs #7) | Dev |
| 10 | Vite build pipeline | P3 | 🟡 Pending | Dev |
| 11 | Audit log monitoring | P4 | 🟡 Pending | IT |
| 12 | JWT secret rotation | P4 | 🟡 Pending | Dev |
| 13 | Session expiry review | P4 | 🟡 Pending | Dev |
