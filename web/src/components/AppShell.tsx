import { useEffect, useRef, useState } from "react";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { Link as RouterLink, Outlet, useLocation, useMatch, useNavigate } from "react-router-dom";
import { useAuth, useUser, type Role } from "../auth/AuthProvider";
import { destinations, pageTitle, type Destination } from "../nav";
import { LogoMark } from "./Icons";

function NavItem({ item, fill }: { item: Destination; fill: boolean }) {
  const active = useMatch({ path: item.to, end: item.end }) !== null;
  return (
    <ButtonBase
      component={RouterLink}
      to={item.to}
      aria-current={active ? "page" : undefined}
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 0.5,
        width: fill ? "100%" : 80,
        minHeight: 56,
        py: 1,
        borderRadius: 3,
        color: active ? "text.primary" : "text.secondary",
      }}
    >
      <Box
        sx={{
          width: 64,
          height: 32,
          borderRadius: 4,
          display: "grid",
          placeItems: "center",
          bgcolor: active ? "primary.light" : "transparent",
          color: active ? "primary.dark" : "inherit",
          transition: "background-color 150ms",
        }}
      >
        <item.Icon />
      </Box>
      <Typography variant="caption" sx={{ fontWeight: active ? 700 : 500 }}>
        {item.label}
      </Typography>
    </ButtonBase>
  );
}

const ROLE_LABEL: Record<Role, string> = { administrator: "Administrator", staff: "Staff", consultant: "Consultant" };

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("");
}

function AccountMenu() {
  const user = useUser();
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [failed, setFailed] = useState(false);
  return (
    <>
      <IconButton
        aria-label={`Account menu for ${user.full_name}`}
        onClick={(e) => { setAnchor(e.currentTarget); setFailed(false); }}
        sx={{ width: 48, height: 48, bgcolor: "primary.light", color: "primary.dark", fontSize: 15, fontWeight: 600 }}
      >
        {initials(user.full_name)}
      </IconButton>
      <Menu anchorEl={anchor} open={anchor !== null} onClose={() => { setAnchor(null); }}>
        <MenuItem disabled sx={{ opacity: "1 !important", display: "block" }}>
          <Typography sx={{ fontWeight: 500 }}>{user.full_name}</Typography>
          <Typography variant="body2" color="text.secondary">{ROLE_LABEL[user.role]}</Typography>
        </MenuItem>
        <MenuItem onClick={() => { setAnchor(null); void navigate("/account/password"); }}>Change password</MenuItem>
        <MenuItem
          onClick={() => {
            logout().then(() => { setAnchor(null); }).catch(() => { setFailed(true); });
          }}
        >
          Sign out
        </MenuItem>
        {failed && (
          <MenuItem disabled sx={{ opacity: "1 !important", color: "error.main", whiteSpace: "normal", maxWidth: 260 }}>
            Could not sign out. Check your connection and try again.
          </MenuItem>
        )}
      </Menu>
    </>
  );
}

function Rail() {
  const user = useUser();
  return (
    <Box
      component="nav"
      aria-label="Main"
      sx={{
        position: "sticky",
        top: 0,
        height: "100dvh",
        width: 96,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 1,
        pt: 3,
        borderRight: 1,
        borderColor: "divider",
      }}
    >
      <Box sx={{ mb: 3 }}>
        <LogoMark size={40} />
      </Box>
      {destinations.filter((d) => d.roles.includes(user.role)).map((d) => (
        <NavItem key={d.to} item={d} fill={false} />
      ))}
      <Box sx={{ mt: "auto", mb: 3 }}>
        <AccountMenu />
      </Box>
    </Box>
  );
}

function TopBar({ title }: { title: string }) {
  return (
    <Box
      component="header"
      sx={{
        position: "sticky",
        top: 0,
        zIndex: 10,
        bgcolor: "background.default",
        borderBottom: 1,
        borderColor: "divider",
        pt: "env(safe-area-inset-top, 0px)",
      }}
    >
      <Box sx={{ height: 64, px: 2, display: "flex", alignItems: "center", gap: 1.5 }}>
        <LogoMark size={28} />
        <Typography variant="h2" component="p" sx={{ flex: 1 }}>
          {title}
        </Typography>
        <AccountMenu />
      </Box>
    </Box>
  );
}

function BottomBar() {
  const user = useUser();
  return (
    <Box
      component="nav"
      aria-label="Main"
      sx={{
        position: "fixed",
        insetInline: 0,
        bottom: 0,
        zIndex: 10,
        display: "flex",
        bgcolor: "background.paper",
        borderTop: 1,
        borderColor: "divider",
        px: 1,
        pt: 1,
        pb: "calc(8px + env(safe-area-inset-bottom, 0px))",
      }}
    >
      {destinations.filter((d) => d.roles.includes(user.role)).map((d) => (
        <NavItem key={d.to} item={d} fill />
      ))}
    </Box>
  );
}

export function AppShell() {
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"), { noSsr: true });
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const firstRender = useRef(true);
  const title = pageTitle(pathname);

  useEffect(() => {
    document.title = `${title} · Ledgerline`;
    // After navigating, move focus to the page so screen readers announce it.
    if (firstRender.current) firstRender.current = false;
    else mainRef.current?.focus();
  }, [pathname, title]);

  return (
    <Box sx={{ minHeight: "100dvh", display: "flex", bgcolor: "background.default", color: "text.primary" }}>
      <Box
        component="a"
        href="#main"
        sx={{
          position: "absolute",
          left: 8,
          top: -64,
          zIndex: 20,
          px: 2,
          py: 1.5,
          borderRadius: 2,
          bgcolor: "primary.main",
          color: "primary.contrastText",
          "&:focus": { top: 8 },
        }}
      >
        Skip to content
      </Box>
      {wide && <Rail />}
      <Box sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        {!wide && <TopBar title={title} />}
        <Box
          component="main"
          id="main"
          tabIndex={-1}
          ref={mainRef}
          sx={{
            flex: 1,
            width: "100%",
            maxWidth: 960,
            mx: "auto",
            p: { xs: 2, md: 5 },
            pb: { xs: "calc(112px + env(safe-area-inset-bottom, 0px))", md: 5 },
            outline: "none",
          }}
        >
          <Outlet />
        </Box>
      </Box>
      {!wide && <BottomBar />}
    </Box>
  );
}
