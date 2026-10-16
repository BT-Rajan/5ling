# Running Ledgerline: backups, monitoring, load and accessibility (C31)

This covers BRD NFR-AV-01/02 (availability, recovery), NFR-OB-01 (monitoring), NFR-PF-01 and NFR-SC-01 (performance, scale) and NFR-US-01 (accessibility, browsers). Install the units in `deploy/systemd/` as described in the README; the server needs `mysqldump`, `mysql`, `openssl`, `gzip` and `rsync`.

## 1. Backups and restore tests (NFR-AV-02)

Targets: recovery point no more than 24 hours, recovery time no more than 4 hours, a restore tested every quarter.

**Daily backup**: `ledgerline-backup.timer` runs `scripts/backup` at 02:15 India time. It writes the database (mysqldump with triggers, compressed, AES-256 with the backup passphrase) and copies the document store (already encrypted with `LL_STORAGE_KEY`) to `LL_BACKUP_TARGET`, which must be another machine or disk. A failure alerts the technical owner at once.

Settings (in `/opt/ledgerline/.env`):

| Setting | Meaning |
| --- | --- |
| `LL_BACKUP_TARGET` | Where backups go: a mounted disk on another machine, or `user@host:/path` for rsync over SSH. Not this server's own disk. |
| `LL_BACKUP_KEY_FILE` | A file holding the backup passphrase (`openssl rand -base64 48 > file; chmod 600 file`). |
| `LL_BACKUP_DIR` | Local working folder (default `/var/backups/ledgerline`). |
| `LL_BACKUP_KEEP_DAYS` | Database backups kept locally (default 30). Keep at least 90 days on the target. |
| `LL_RESTORE_DATABASE_URL` | An empty scratch database for restore tests; never the live one (the script refuses it). |

Keep copies of `LL_BACKUP_KEY_FILE`'s passphrase and of `LL_STORAGE_KEY` somewhere safe and apart from the backups (a password manager, a sealed envelope with a Partner). Without them a backup cannot be read; with the backups they would defeat the encryption.

**Quarterly restore test**: `ledgerline-restore-test.timer` runs `scripts/restore-test` on the 5th of January, April, July and October. By hand: `scripts/restore-test` (newest backup) or `scripts/restore-test FILE`. It checks the backup's checksum, restores it into the scratch database, and then checks:

- the schema is at the latest migration;
- the audit log's chain is intact;
- the main tables have rows;
- 20 sampled documents from the backed-up store decrypt and match their checksums;
- the backup's age is within the 24-hour recovery point.

The result, with the load and total times (the measured recovery time), is appended to `/var/backups/ledgerline/restore-tests.log`. Keep that log with the sign-off pack.

**Full recovery** (the server is lost): build a new server from the README, restore `LL_STORAGE_KEY`, the environment file and the backup passphrase, copy `storage/` back from the target, restore the newest database backup with the same commands `scripts/restore-test` uses (into the live database name), run `alembic upgrade head`, then `python -m app.cli verify-audit` and `security-check`. The restore test's timings show how long the database part takes.

## 2. Monitoring (NFR-OB-01)

The technical owner (`LL_TECH_OWNER_EMAIL`, required in production) is emailed directly through the mail server, not through the outbox, so the alert still goes out if the email worker is the thing that failed:

| Alert | Raised by | When |
| --- | --- | --- |
| A service failed (any Ledgerline job, the email worker, the monitor itself) | `OnFailure=ledgerline-notify@%n.service` in every unit | At once |
| A job run recorded as failed (the cycle generator) | `ledgerline-monitor.timer`, every minute | Within 1 minute of the run being recorded |
| The cycle generator has not succeeded for 26 hours | monitor | Once a day while it lasts |
| Client emails failed or bounced | monitor | Once an hour while they keep failing |
| Server errors: `LL_ERROR_SPIKE_THRESHOLD` (default 10) or more in 5 minutes | monitor (the API counts every 5xx per minute) | Once per 10 minutes while it lasts |

Every alert is recorded in `ops_alerts` once per key, so the monitor never repeats itself; an alert the mail server refused is tried again each minute, up to five times, and every alert is also logged at CRITICAL. To test that alerts arrive within 5 minutes (the BRD's acceptance test): `sudo systemctl start ledgerline-notify@test.service`, and the technical owner should have "Service failed: test" within a minute.

Availability (NFR-AV-01, 99.5% in working hours) is measured from outside: point an uptime checker at `https://<host>/api/health/ready` every minute and keep its monthly report.

## 3. Load test (NFR-PF-01, NFR-SC-01)

Targets: screens under 2 seconds and searches under 1 second, with 5,000 clients and 50,000 cycles.

```
cd api
export LL_SECRET_KEY=... LL_DATABASE_URL=mysql+pymysql://owner:pw@127.0.0.1:3306/ledgerline_load
.venv/bin/alembic upgrade head        # an empty database whose name ends in _load
.venv/bin/python tools/loadtest.py    # seeds 5,000 clients the first time, then times
```

The first run seeds the database through the real API: 20 owners, one package of two monthly filings, and 5,000 clients. The cycle generator gives them about 190,000 cycles, more than the target. Later runs reuse the seeded data. The tool then times the client search (by name and PAN), the ticket search, the client list and page, a ticket, the work list and dashboard (Administrator and Staff), a dashboard list and two reports, 20 times each. It prints each one's 95th percentile against its target and exits 1 if any miss. Times are server time, in process; run it on the production-sized server and keep the output with the sign-off pack.

## 4. Accessibility and browsers (NFR-US-01)

Automated, in every build:

- **Colour contrast:** `web/src/tokens.test.ts` proves every text colour pair meets WCAG 2.1 AA (4.5:1), in light and dark mode.
- **Keyboard and names:** `web/src/a11y.test.tsx` checks the sign-in screen, the home dashboard and the reports screen. Every control must have a name a screen reader can read, and the Tab key must reach every control in turn.
- **Screens built for this:** each screen has one page heading, labelled lists and tables, buttons rather than clickable text, and 48-pixel touch targets.

By hand, before go-live and after large changes (record who, when and the result):

| Check | Chrome | Edge | Safari | Firefox | Phone (client portal) |
| --- | --- | --- | --- | --- | --- |
| Sign in with two-step code |  |  |  |  | n/a |
| Daily work list, using only the keyboard |  |  |  |  | n/a |
| Client page, upload a document, record a payment |  |  |  |  | n/a |
| Ticket: review, approve, mark filed |  |  |  |  | n/a |
| Reports: view, download spreadsheet and PDF |  |  |  |  | n/a |
| Client portal link: open, upload from camera roll |  |  |  |  |  |
| Screen reader (NVDA on Windows, VoiceOver on Mac/iPhone): work list and portal |  |  |  |  |  |
| 200% zoom, no lost content |  |  |  |  |  |

The usability part of NFR-US-01 (a new coordinator finishes the daily work list after an hour of training) is tested with a real coordinator during the pilot (C32).

## 5. WhatsApp or SMS (C36; BRD INT-02, FR-AL-08, C-2, open question Q2)

Email is always sent: it is the minimum channel. WhatsApp or SMS goes as well, and only when all of these hold:

- the client's primary contact has agreed to that channel, by their latest consent record on the client page;
- the contact has a mobile number;
- the message's template is approved.

WhatsApp is used if they agreed to both. Consent is checked again just before sending, so a client who opts out in the meantime is never messaged. If a message fails, the email has already gone (the fallback), and the client's owner gets a notification so they can call. Temporary failures are tried three times, on the next runs of the alerts job. Every message and its outcome is listed on the ticket, under "WhatsApp and SMS".

It is **off** until the firm picks a provider (Q2). To switch it on:

1. Have the provider approve the two templates in `api/app/messaging/templates.py` (`first_alert`, `reminder`), word for word. For SMS, register them on DLT too. They carry no link or PAN; the private link is only in the email.
2. Add the provider: one class with a `send` method in `api/app/messaging/provider.py`, and its name in `LL_MESSAGING_PROVIDER` and `provider_from_settings`. Its credentials go in the environment file.
3. Set `LL_MESSAGING_PROVIDER` and `LL_MESSAGING_APPROVED_TEMPLATES=first_alert,reminder`. Only listed templates are ever sent.

To try it before a provider is chosen, set `LL_MESSAGING_PROVIDER=log`. Each message is then recorded and logged, showing only the last four digits of the number, and nothing is sent.
