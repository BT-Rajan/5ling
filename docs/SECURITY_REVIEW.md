# Ledgerline security review (C30)

This is the review behind chunk C30 (BRD NFR-SE-04, NFR-SE-06, NFR-SE-07, FR-AC-01). It records what was checked in the code, what each installation must check for itself, and what the independent tester is asked to do. `SECURITY.md` lists every control by chunk; this document is the pre-go-live review.

## 1. Encryption at rest and key separation (LL-125, NFR-SE-04)

| What | How it is protected | Key | Where the key lives |
| --- | --- | --- | --- |
| Client documents | Each file is encrypted with its own random AES-256-GCM key before it is written; that key is sealed with the master key; the storage path is bound in as associated data, so a file cannot be swapped for another. Files are never overwritten. (`api/app/files/store.py`) | `LL_STORAGE_KEY` (32 random bytes) | The server's environment file, readable only by the service account. Not in the database, not in the repository. Production refuses to start without it. |
| Two-step sign-in secrets | Encrypted in the database (Fernet, key derived from the session secret with its own label). | `LL_SECRET_KEY` | Environment file. |
| Session tokens, client link tokens, recovery codes | Stored only as hashes (SHA-256 or Argon2); the tokens themselves are never stored. | none needed | |
| Passwords | Argon2id. | none needed | |
| The database (everything else) | MySQL InnoDB data-at-rest encryption: tables, redo, undo and binary logs. Set up on the server (steps below). | MySQL keyring | A keyring file (or vault) on the database server, outside the data directory and backups. |
| Backups | Taken from the encrypted database; the storage directory holds only encrypted files. Backups do not contain `LL_STORAGE_KEY` or the keyring. | | (C31) |

Key separation: the document key, the session secret and the database keyring are three different secrets, held in three places (the app's environment file, the same file under a different name, and the database server's keyring). A copy of the database alone, or of the storage directory alone, or of both, reveals no document.

Bank details: Ledgerline does not store bank account details for clients. Payments record a reference (UTR, cheque number) only.

### Checking an installation

Run, on the app server, as the service user:

```
python -m app.cli security-check
```

It prints PASS or FAIL for each item and exits 1 if anything fails:

- production mode; the document key is set and kept apart from the session secret and the database URL;
- every Ledgerline table is encrypted at rest; new tables are encrypted by default;
- the redo, undo and binary logs are encrypted;
- the database server requires TLS from every client.

To make the database checks pass (MySQL 8, once, before go-live):

1. Install the keyring component (`component_keyring_file` or a vault-backed keyring) with its key file outside the data directory and outside backups, readable only by `mysql`.
2. In `my.cnf`: `default_table_encryption=ON`, `innodb_redo_log_encrypt=ON`, `innodb_undo_log_encrypt=ON`, `binlog_encryption=ON`, `require_secure_transport=ON` (with a server certificate).
3. Encrypt the existing tables: `ALTER TABLE <each table> ENCRYPTION='Y';`
4. Add `?ssl_ca=...` (or the equivalent) to the database URLs in the app's environment file.
5. Run `security-check` again until every line says PASS. Keep its output with the sign-off pack (C32).

## 2. Personal identifiers on screen and in logs (LL-124, NFR-SE-06)

- The PAN is masked everywhere on screen (`AB•••••34F`). The client API sends only the masked form; the full PAN comes from a separate request, made when someone presses Show, and each such request is written to the audit log (`client.pan_revealed`, without the PAN).
- Logs: every log line passes through a redacting formatter (C00) that removes PANs, GSTINs, email addresses, long numbers (bank accounts, phone numbers, Aadhaar) and secrets, including from exception tracebacks. Requests are logged without their query strings.
- Log review, automated: `api/tests/test_hardening.py` runs the screens that handle PANs and GSTINs (create, search by PAN and GSTIN, duplicates, invalid input, open, reveal, refused access) and checks that none appears in any log line as written, or in any audit entry.

## 3. Authorization against the BRD (LL-126, FR-AC-01, NFR-SE-01)

- `api/tests/test_authz_matrix.py` reads the role table from section 8 of the BRD itself (`docs/Ledgerline-BRD.docx`) and checks that the matrix in `api/tests/brd_matrix.py` matches it cell for cell. All sixteen capabilities now have their routes.
- Every route is claimed by a capability, is self-service, or is on the short public list; a new route fails the build until it is placed.
- Every route is called as every role; the roles that get in must be exactly the matrix's. Every state-changing route refuses a missing CSRF token and a foreign origin.
- Record-level checks (own clients, assigned task only, break-glass for Administrators opening others' clients) have their own tests in each chunk.
- No file is reachable without signing in (`test_hardening.py`): every upload and download route answers 401 to a stranger; download links are personal and expire in five minutes; stored files contain no plain text.

## 4. Internal review results (this chunk)

| Check | Result |
| --- | --- |
| `bandit` (Python security lint), app and migrations | No issues |
| `pip-audit` on `api/requirements.txt` | No known vulnerabilities |
| `npm audit --omit=dev` (web) | 0 vulnerabilities |
| Authorization matrix against BRD section 8 | Matches; every capability built |
| Log and audit review for PAN and GSTIN | None found |
| Unauthenticated file access | None |

Run the same checks again on the release candidate and attach the output to the sign-off pack.

## 5. Independent security test (LL-127, NFR-SE-07)

This needs a tester who is independent of the build. It cannot be done from inside the project. Suggested scope for the request:

- Web application test of the staff app, the consultant sign-in and the client portal, against the OWASP ASVS level 2 and the OWASP Top 10, on a staging copy with test data only.
- Authorization: horizontal (Staff A against Staff B's clients; one consultant against another's work; one client link against another client) and vertical (Staff and consultant against Administrator functions).
- Session handling, two-step sign-in, CSRF, client links (reuse, expiry, guessing), the file upload and download paths (type checks, malware handling, link reuse), report exports, and the audit log's tamper evidence.
- Infrastructure: TLS configuration, security headers, the database server's exposure and encryption settings.

Accounts to give the tester: one Administrator, two Staff, two consultants, and client links for two clients, on staging. Findings rated critical or high must be fixed and retested before go-live; the tester's report is signed by the firm (BRD NFR-SE-07) and kept with the sign-off pack. Repeat yearly.
