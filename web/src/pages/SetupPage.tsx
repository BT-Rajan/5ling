import type { ComponentType } from "react";
import ButtonBase from "@mui/material/ButtonBase";
import Stack from "@mui/material/Stack";
import type { SvgIconProps } from "@mui/material/SvgIcon";
import Typography from "@mui/material/Typography";
import { Link as RouterLink } from "react-router-dom";
import { CalendarIcon, ClientsIcon, TeamIcon, WorkListIcon } from "../components/Icons";

const SECTIONS: readonly { to: string; title: string; text: string; Icon: ComponentType<SvgIconProps> }[] = [
  { to: "/setup/team", title: "Team", text: "People who can sign in, their roles and two-step sign-in.", Icon: TeamIcon },
  { to: "/setup/filings", title: "Filing calendars", text: "Filing types, their due-date rules and alert lead times.", Icon: WorkListIcon },
  { to: "/setup/holidays", title: "Holidays and lead times", text: "Non-working days, and how many days before a due date clients are alerted.", Icon: CalendarIcon },
  { to: "/setup/extensions", title: "Due-date extensions", text: "Move open cycles to a new due date when the government extends one.", Icon: CalendarIcon },
  { to: "/setup/packages", title: "Packages", text: "What a client is given: filings, document checklists and the fee.", Icon: ClientsIcon },
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
