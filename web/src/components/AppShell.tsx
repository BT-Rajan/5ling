import { useEffect, useRef } from "react";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import { Link as RouterLink, Outlet, useLocation, useMatch } from "react-router-dom";
import { destinations, type Destination } from "../nav";
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

function Rail() {
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
      {destinations.map((d) => (
        <NavItem key={d.to} item={d} fill={false} />
      ))}
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
        <Typography variant="h2" component="p">
          {title}
        </Typography>
      </Box>
    </Box>
  );
}

function BottomBar() {
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
      {destinations.map((d) => (
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
  const current = destinations.find((d) => (d.end ? pathname === d.to : pathname.startsWith(d.to)));
  const title = current?.label ?? "Not found";

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
