export default function Home() {
  return (
    <main>
      <nav>
        <strong>Market Brief Agents</strong>
        <a href="/daily-recap">Daily Recap</a>
        <a href="/archives">Archives</a>
        <a href="/about">About</a>
      </nav>
      <section className="hero">
        <div>
          <h1>Market Brief Agents</h1>
          <p>Short, educational financial news recaps generated from market data, headlines, and filings.</p>
        </div>
      </section>
      <section>
        <h2>Daily Recap</h2>
        <article className="empty-state">
          <span>Review queue</span>
          <h3>Reviewed recaps will appear here</h3>
          <p>
            The local pipeline is set up to publish educational market recaps after analysis,
            script generation, and editorial approval.
          </p>
        </article>
      </section>
      <section className="newsletter">
        <h2>Newsletter Signup</h2>
        <form>
          <input aria-label="Email" placeholder="you@example.com" type="email" />
          <button type="submit">Join</button>
        </form>
      </section>
    </main>
  );
}
