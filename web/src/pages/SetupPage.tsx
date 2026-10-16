import type { ComponentType } from "react";
import ButtonBase from "@mui/material/ButtonBase";
import Stack from "@mui/material/Stack";
import type { SvgIconProps } from "@mui/material/SvgIcon";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { AuditIcon, CalendarIcon, ClientsIcon, TeamIcon, WorkListIcon } from "../components/Icons";

const SECTIONS: readonly { to: string; title: string; text: string; Icon: ComponentType<SvgIconProps> }[] = [
  { to: "/setup/team", title: "Team", text: "People who can sign in, their roles and two-step sign-in.", Icon: TeamIcon },
  { to: "/setup/filings", title: "Filing calendars", text: "Filing types, their due-date rules and alert lead times.", Icon: WorkListIcon },
  { to: "/setup/holidays", title: "Holidays and lead times", text: "Non-working days, and how many days before a due date clients are alerted.", Icon: CalendarIcon },
  { to: "/setup/extensions", title: "Due-date extensions", text: "Move open cycles to a new due date when the government extends one.", Icon: CalendarIcon },
  { to: "/setup/email", title: "Email", text: "Every email sent and how it went; send a test email.", Icon: AuditIcon },
  { to: "/setup/packages", title: "Packages", text: "What a client is given: filings, document checklists and the fee.", Icon: ClientsIcon },
  { to: "/setup/rules", title: "Rules and access", text: "Reminder and escalation timings, link expiry, idle sign-out; who opened clients by break-glass.", Icon: AuditIcon },
  { to: "/setup/access-review", title: "Access review", text: "Everyone with access to each client, to export and certify each quarter.", Icon: TeamIcon },
  { to: "/setup/fees", title: "Fees and tax", text: "Approve client-specific fees; set the GST rate used on invoices.", Icon: AuditIcon },
];

export function SetupPage() {
  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 3, display: { xs: "none", md: "block" } }}>Setup</Typography>
      <Stack spacing={2} sx={{ maxWidth: 720 }}>
        {SECTIONS.map(({ to, title, text, Icon }) => (
          <ButtonBase
            key={to}
            component={RouterLink}
            to={to}
            sx={{ justifyContent: "flex-start", textAlign: "left", gap: 2, p: 2.5, border: 1, borderColor: "divider", borderRadius: 3 }}
          >
            <Icon color="primary" />
            <span>
              <Typography component="span" variant="h6" sx={{ display: "block" }}>{title}</Typography>
              <Typography component="span" color="text.secondary">{text}</Typography>
            </span>
          </ButtonBase>
        ))}
      </Stack>
    </>
  );
}
