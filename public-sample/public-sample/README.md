# MailPilot public sample workspace

Open https://mailpilot-workspace.duckdns.org/ to try it. No account, download or terminal is needed.

This browser-only sample includes fictional Cedar Studio and Harbor Books messages, literal search, message detail, potential action review, local decisions/history, and a two-tick fake sync. Decisions and sample sync progress stay in this browser's local storage. Reset sample clears both fictional mailboxes on this browser. There are no external API calls, tracking resources, AI requests or real-mail operations in this sample.

The separate Google pilot is restricted to its configured test account. This sample is not a claim that public Gmail onboarding, production encryption, Google verification or the full private-mail release is complete. Signing into the private pilot never makes mail part of this fictional sample.

## Source and hosting

The three assets here are the deployed sample source: index.html, app.css and app.js. The public host serves index.html at `/` and the other assets at `/demo-assets/`. Existing private pilot routes remain separate. The code uses no external packages or assets. Its local storage contains fictional choices only; it is not authentication or a security boundary for real mail.
