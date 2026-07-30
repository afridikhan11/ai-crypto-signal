# Final Beta Release Checklist

Date: 2026-07-28
Covers: Beta Readiness audit (2026-07-28), Beta Hardening (Phase 8), WPF Integration (Phase 9). Full detail for each item lives in `BETA_READINESS_REPORT.md`, `BETA_HARDENING_REPORT.md`, and `WPF_INTEGRATION_REPORT.md`.

## Must do before you consider this beta "live" for anyone but you

1. **Run `dotnet build` on the WPF project.** This is the one thing I genuinely cannot verify myself in this sandbox (no .NET SDK here). Phase 9 added 3 new screens, 3 new ViewModels, ~29 new DTOs, and one new converter - all manually cross-checked and, per `WPF_INTEGRATION_REPORT.md` section 4, partially verified by a live XAML markup-compile watcher on your machine, but never through a full C# build in my hands.
2. **Click through the 3 new screens** (AI Assistant, Portfolio Intelligence, AI Performance) end to end - this is the real end-to-end validation your Phase 9 instructions asked for, which I could not perform myself. Try: a normal analysis question, a follow-up question, a coaching question, Portfolio Intelligence with and without a linked Binance account, and both AI Performance tabs including the date filter.
3. **Generate a real `SECRET_KEY`** (`python scripts/generate_secret_key.py`) before deploying anywhere reachable outside your own machine - still on the default placeholder in dev, which is expected, but must change before that.

## Should do soon, not urgent

4. Decide on Market/Portfolio WPF dead-file cleanup (confirmed unreachable via navigation, still present, pending your approval per your standing "never delete without approval" rule).
5. Bump `pyjwt` install to the now-pinned 2.13.0 next time you rebuild your Docker image (`requirements.txt` already updated).
6. Consider `REQUIRE_AUTH=true` + real admin password once you're ready for the WPF app to actually log in (today it doesn't send an Authorization header at all - see `app/core/security.py`'s docstring for what that follow-up would involve).
7. Run `pip-audit` locally for a complete dependency-vulnerability sweep (I could only check a few security-sensitive packages via web search from this sandbox - see `BETA_READINESS_REPORT.md` section on dependency health).

## Already done, no action needed

- All 7 approved Beta Hardening items (logging, warning logs, date-range filtering, PyJWT bump, global exception handler, startup validation warnings) - see `BETA_HARDENING_REPORT.md`. One of the 7 (WebSocket disconnect cleanup) turned out to already be correct - no fix was needed, and I said so rather than making a no-op change.
- Beta audit found **zero blockers** - see `BETA_READINESS_REPORT.md`.
- Full backend `python3 -m py_compile` passes, custom static-analysis scanner shows no new issues, and the Phase 6/7 regression suite still passes after every Phase 8 change.
- Market/Portfolio WPF screens conclusively confirmed unreachable from the app's real navigation (not just "probably dead" - traced through the actual `MainWindow.xaml.cs` switch statement).

## Not done, and deliberately out of scope for this beta round

- Login flow for the WPF app (auth exists in the backend, unused by the desktop client - a real follow-up, not a bug).
- Any new backend API, AI engine, or scoring system - none were added, per your explicit "architecture frozen" instruction across Phases 8 and 9.

---

Everything above traces back to a specific finding in one of the three linked reports - nothing here is new information, just gathered into one list. No further code changes have been made since the WPF Integration work above. Waiting for your review before Version 1.0 development begins.
