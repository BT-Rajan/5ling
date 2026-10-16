# Ledgerline go-live: catalog, migration, pilot and sign-off (C32)

This covers BRD sections 14 and 15 and open questions Q4 (filing types and due-date rules) and Q5 (migration volume and source). Work through it in order. Each step names who does it and what is kept for the sign-off pack.

Before you start, do the following:

- Install the server as described in the README.
- Complete `docs/SECURITY_REVIEW.md` section 1: `security-check` must say PASS on every line.
- Install the backup, restore-test and monitor timers (`docs/OPERATIONS.md`).
- Create the first Administrator with `scripts/create-admin`.
- Create the Staff accounts on the Users screen.

The tools below run on the server as the service user, from `/opt/ledgerline/api`, with the environment file loaded. They act as a named Administrator (`--as`). Everything they save goes through the same checks as the screens and is in the audit log under that person's name.

## 1. Filing types, due dates and packages (LL-133, Q4)

`docs/catalog/india-common.json` is a starting catalog of common Indian filings and packages: GSTR-1 and GSTR-3B (monthly and QRMP), CMP-08, GSTR-9, TDS 24Q and 26Q, income tax returns and the tax audit report.

**Every due date and fee in it must be confirmed by the Partners before loading (Q4).** Statutory dates are extended from time to time. An extension is handled afterwards on the Extensions screen, not by editing the catalog.

1. Copy the file, then edit the copy:
   - fees and package names to the firm's own;
   - checklists to what the firm actually asks clients for;
   - add or remove filings.

   Rules for a calendar:
   - `due.months_after` and `due.day` count from the last month of the period. With financial-year handling, Q4 is January to March.
   - `overrides` gives one period its own date. For example, TDS for Q4 is due on 31 May.
   - A day past the month's end means the last day of that month.
2. Check it. Nothing is saved, and each filing's dates for this year are previewed:

   ```
   .venv/bin/python -m app.cli load-catalog /path/catalog.json --as admin@firm.in
   ```

3. A Partner compares the previewed first due dates with the firm's own compliance calendar and signs the copy. Keep the signed copy for the sign-off pack.
4. Load it:

   ```
   .venv/bin/python -m app.cli load-catalog /path/catalog.json --as admin@firm.in --commit
   ```

   Filings whose code and packages whose name already exist are left alone. Run it again after adding entries.
5. Check the Filings and Packages screens. Later changes to a calendar or package are made there, as new versions.

## 2. Client migration (LL-132, Q5)

**Source and volume (Q5).** Export the clients from the current system, whether a spreadsheet or the old practice software, into one CSV file in UTF-8. Put a header row first, using these columns; the template is `docs/catalog/clients-template.csv`:

| Column | Required | Meaning |
| --- | --- | --- |
| `source_id` | yes | The client's id in the old system. It names the row in reports and in the id map, so the PAN never has to. |
| `kind` | yes | `individual` or `business` |
| `legal_name` | yes | As on the PAN or GST registration |
| `pan` | for individuals | Checked for form; must be unique |
| `gstin` | if registered | Checked for form and check digit; must be unique |
| `owner_email` | yes | An active Staff member or Administrator, who owns the client in Ledgerline |
| `package` | yes | The name of a published package (any case) |
| `contact_name` | yes | The primary contact |
| `contact_email` | yes | Email is always on for the primary contact |
| `contact_phone` | if WhatsApp or SMS is yes | An Indian mobile number may be given without +91 |
| `whatsapp`, `sms` | no | `yes` or `no` (empty is no): consent given by the client |

Only the primary contact comes across; add any others on the client screen. The file may hold up to 20,000 rows and 20 MB. For more than that, split the file and run each part.

The export holds PANs and contact details. Keep it on the server only (`chmod 600`), and delete it once the migration is verified (step 5).

1. **Check** the file. Nothing is saved:

   ```
   .venv/bin/python -m app.cli migrate-clients clients.csv --as admin@firm.in
   ```

   Every problem is listed by line and source id: a malformed PAN or GSTIN, an unknown owner or package, the same PAN twice in the file, a PAN and GSTIN belonging to two different existing clients, and so on. The report never shows a PAN, GSTIN, email or phone number. Fix the source file and check again until no row has a problem.
2. **Save** the rows:

   ```
   .venv/bin/python -m app.cli migrate-clients clients.csv --as admin@firm.in --commit
   ```

   This is refused while any row has a problem. Each row is saved as if typed in, and its cycles are made at once.

   A row whose PAN or GSTIN is already in Ledgerline is counted as "already in Ledgerline" and left alone, so a run that stopped part-way can be run again.

   The id map, `clients.map.csv` (source id, Ledgerline client id, result; no PAN), is written next to the file. Keep it: it is how a question about an old client is traced.
3. **Count check and sample check** run after saving and are printed as PASS or FAIL:
   - *count*: every row of the file is a client in Ledgerline, found by its PAN or GSTIN;
   - *sample*: 5% of the rows, at least one, chosen at random, are compared field by field with what Ledgerline holds. The fields are kind, legal name, PAN, GSTIN, owner, package, and the contact's name, email and phone.

   Both checks can be run again on their own at any time:

   ```
   .venv/bin/python -m app.cli migrate-clients clients.csv --as admin@firm.in --verify
   ```

4. **Human sample.** A Partner or the practice manager opens 10 of the migrated clients, picked from the id map, and compares them with the old system: open filings, owner and fee. Record who checked, when and the result in the sign-off pack.
5. Keep the check output and the id map for the pack. Delete the source CSV: `shred -u clients.csv`.

For a few clients at a time after go-live, Staff and Administrators can use **Clients → Import** in the app. It uses the same columns and checks; rows with problems are listed and left out, and the rest are saved only after the person confirms.

**Open work at cut-over.** Migrated clients get their cycles from the joining date onward, like any new client. Work already in progress in the old system for an earlier period (a return being prepared now) is entered as an ad hoc task on the client, or finished in the old system. Agree which with the Partners before cut-over. Old documents stay in the old system's archive; they are not migrated.

## 3. Pilot (LL-134, BRD section 14)

**Scope.** 20 real clients, chosen by the Partners. They cover every package in use, at least two owners, at least one client using the portal from a phone, and at least one consultant assignment. The pilot runs in production with the real data. Clients outside the pilot are still run in the old system.

**Duration and exit.** The pilot runs at least two weeks and covers at least one monthly GST due date. It ends when two weeks in a row pass with no open severity 1 defect, and every severity 2 defect is fixed or has a workaround the Partners accept.

**Severities.**

| Severity | Meaning | Response |
| --- | --- | --- |
| 1 | Wrong or lost data; a filing could be missed or mis-dated; a client could see another client's information; anyone gets access they should not; the app is down in working hours | Stop and fix now; the two-week clock restarts after the fix |
| 2 | A main task (work list, documents, review, filing, fees) cannot be done the normal way, but there is a workaround | Fixed before go-live |
| 3 | Inconvenient, cosmetic or a wording problem | Logged; fixed when convenient |

**Triage.** The practice manager logs every problem the same day in the defect log below. Twice a week the practice manager, a Partner and the technical owner go through the open defects: they set the severity, decide fix or workaround, and set a date. A defect is closed only after it is checked again in the app.

**Defect log** (keep it as a spreadsheet with these columns):

| No. | Date found | Found by | Screen or job | What happened | Severity | Decision | Fixed in (version) | Checked by, date | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

**During the pilot, also check:**

- **Usability (NFR-US-01).** A coordinator new to the app gets an hour's training, then finishes the day's work list without help. Record the time taken and the questions asked.
- **The daily jobs.** Look at the timers (`systemctl list-timers 'ledgerline-*'`), the monitor alerts and the audit check every morning for the first week.
- **The browser and accessibility checklist** in `docs/OPERATIONS.md` section 4.
- **Client feedback.** Ask the pilot clients about the portal and the emails.

## 4. Sign-off pack (LL-135, BRD section 15)

Run `scripts/signoff-evidence` on the production server at the end of the pilot. It writes a dated folder with:

- the schema version;
- the audit chain check;
- `security-check`;
- the latest restore tests;
- the job timers;
- a check that no Ledgerline service has failed.

It exits 1 if any of them fails. Add the following to that folder:

| Item | From |
| --- | --- |
| Automated test results for the released version (API and web), with the role matrix checked against BRD section 8 | CI run for the release |
| `scripts/signoff-evidence` output | above |
| Load test output on the production server (NFR-PF-01, NFR-SC-01) | `docs/OPERATIONS.md` section 3 |
| Restore test log (NFR-AV-02) | `/var/backups/ledgerline/restore-tests.log` |
| Monitoring test: the alert from `ledgerline-notify@test.service` arrived within 5 minutes (NFR-OB-01) | `docs/OPERATIONS.md` section 2 |
| Browser and accessibility checklist, filled in (NFR-US-01) | `docs/OPERATIONS.md` section 4 |
| Internal security review and the independent security test report, with critical and high findings closed (NFR-SE-07) | `docs/SECURITY_REVIEW.md` |
| Signed catalog (Q4) | section 1 |
| Migration check output, id map, and the human sample record | section 2 |
| Pilot defect log, with no open severity 1 or 2 and two clean weeks | section 3 |
| Usability observation (NFR-US-01) | section 3 |

**Acceptance.** The Partners sign a one-page acceptance that names the following:

- the released version (the code line in the evidence summary);
- the date;
- any accepted severity 3 defects;
- any open items with owners and dates.

Go-live for all clients follows the signature: migrate the remaining clients as in section 2, then stop using the old system for new work.
