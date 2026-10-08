import Typography from "@mui/material/Typography";

export function EmptyPage({ title, children }: { title: string; children: string }) {
  return (
    <>
      <Typography variant="h1" component="h1" sx={{ mb: 2, display: { xs: "none", md: "block" } }}>
        {title}
      </Typography>
      <Typography color="text.secondary" sx={{ maxWidth: 480 }}>
        {children}
      </Typography>
    </>
  );
}
