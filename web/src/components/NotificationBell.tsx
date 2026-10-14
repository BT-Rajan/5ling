import { useEffect, useState } from "react";
import Badge from "@mui/material/Badge";
import IconButton from "@mui/material/IconButton";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { api } from "../api";
import { useUser } from "../auth/AuthProvider";
import { BellIcon } from "./Icons";

const EVERY_MS = 60_000;

/** The unread count, checked every minute and on each page change (FR-IN-01). */
export function NotificationBell() {
  const user = useUser();
  const { pathname } = useLocation();
  const [count, setCount] = useState<{ unread: number; open_escalations: number } | null>(null);

  useEffect(() => {
    if (user.role === "consultant") return;
    let live = true;
    const check = () => {
      api<{ unread: number; open_escalations: number }>("/api/notifications/count")
        .then((c) => { if (live) setCount(c); })
        .catch(() => undefined);
    };
    check();
    const timer = setInterval(check, EVERY_MS);
    return () => { live = false; clearInterval(timer); };
  }, [user.role, pathname]);

  if (user.role === "consultant") return null;
  const unread = count?.unread ?? 0;
  const urgent = (count?.open_escalations ?? 0) > 0;
  const label = unread === 0 ? "Notifications" : `Notifications, ${String(unread)} unread`;
  return (
    <IconButton component={RouterLink} to="/notifications" aria-label={label} sx={{ width: 48, height: 48 }}>
      <Badge badgeContent={unread} color={urgent ? "error" : "primary"} max={99}>
        <BellIcon />
      </Badge>
    </IconButton>
  );
}
