export function WelcomeHero() {
  return (
    <section className="rounded-2xl bg-hero px-6 py-7 text-hero-foreground sm:px-8 sm:py-8 dark:ring-1 dark:ring-white/10">
      <p className="text-xs font-semibold tracking-[0.14em] text-hero-muted uppercase">
        Northstar Insurance · AI assistant
      </p>
      <h1 className="mt-3 font-heading text-2xl font-semibold tracking-tight sm:text-3xl">
        A better conversation starts here.
      </h1>
      <p className="mt-2 max-w-2xl text-base text-hero-foreground/80">
        Tell Maya what you need and schedule a consultation with a licensed agent, simply by
        speaking.
      </p>
    </section>
  );
}
