# TODO

## Queue TTS Across Continue Turns

### Goal
- Prevent newly generated TTS from playing over audio that is already in progress.
- When the user clicks Continue while assistant speech is still playing, generate the continued text normally, but delay its TTS audio until the current speech queue has finished.
- Preserve barge-in behavior: user speech or explicit stop/cancel should still interrupt playback.

### Current Behavior
- The orchestrator serializes TTS chunks inside a single assistant turn with `state.tts_tail`.
- Starting a new text, audio, or continue turn currently calls `cancel_tts(...)`, which cancels pending/current TTS before the new turn starts speaking.
- The browser also has its own Web Audio scheduling queue, so backend and frontend need to agree on when playback is actually finished.

### Proposed Plan
- Add a TTS queue mode for continue turns:
  - Keep current cancellation behavior for new user text, microphone speech, barge-in, manual stop, clear, and TTS preset changes.
  - For `user.continue`, do not cancel active TTS by default.
  - Let continued-turn TTS append behind the existing backend `tts_tail` chain so speech is generated and emitted only after prior TTS tasks finish.
- Track turn ownership clearly:
  - Keep `turn_id` on all `tts.first_chunk` and `tts.audio` events.
  - Ensure a continued turn can stream LLM text immediately while its TTS waits behind existing audio.
  - Avoid resetting the browser playback clock on `turn.started` for continue turns if audio is already scheduled.
- Add a playback completion handshake if backend-only sequencing is not enough:
  - Browser emits an event such as `audio.playback_finished` after its scheduled queue drains.
  - Backend can use this to avoid generating next queued TTS too early when browser-side playback lead time is significant.
  - Keep this optional for the first pass if existing backend `tts_tail` sequencing is sufficient in manual testing.
- Add a small runtime/profile option if needed:
  - Example: `turn.continue_tts_policy = "queue"` by default.
  - Accepted values could be `"queue"` and `"cancel"`, but only add this if tests or usability show users need both modes.

### Edge Cases
- If the user speaks while queued continue TTS is waiting, cancel the queued TTS and stop browser playback.
- If the user clicks Stop Audio, cancel both currently playing and queued backend TTS tasks.
- If the user clicks Continue repeatedly, preserve ordering so each continuation's TTS plays after the previous queued speech.
- If TTS provider/preset/voice changes while queued audio exists, cancel the queue before switching.
- If the LLM continuation errors, do not disturb already playing speech unless the user explicitly cancels.

### Test Plan
- Add orchestrator tests verifying continue does not call `cancel_tts("continue_turn")` when queue mode is active.
- Add a test where a second assistant TTS task waits behind an existing `tts_tail`.
- Add a test that user text still cancels active/queued TTS.
- Add a test that manual `tts.cancel` clears queued TTS.
- Manually test in the browser:
  - Ask for a long spoken response.
  - Click Continue while audio is still playing.
  - Confirm the continued text appears immediately, but its TTS starts only after current audio finishes.
  - Confirm barge-in and Stop Audio still interrupt immediately.
