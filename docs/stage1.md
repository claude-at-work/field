# stage 1 — user story + success criterion

The smallest concrete user-visible behavior Stage 1 must satisfy. Written *before* any Stage 1 code lands, so the story is the criterion the implementation answers to, not a retrofit description of what got built.

## the story

> Tyler is in `~/projects/firmament`. He types `git status`. The new root has no `git` binary. The dispatcher catches the miss, finds `/usr/bin/git` in the kali-fs snapshot, builds a `bwrap` invocation that bind-mounts the snapshot's library tree (`/usr/lib*`, `/lib*`, `/usr/share`, `/etc`) into a fresh mount namespace, exec's git inside the namespace. He sees git's output. The working tree's status renders. He doesn't realize the binary came from a snapshot.
>
> Later he types `python3 --version`. There are two candidates — one in `kali-fs`, one in `kali-march-2026`. The dispatcher prompts once: *"two candidates: kali-fs (python3, last seen never) / kali-march-2026 (python3, last used 14 days ago in /home/tyler/firmament). Pick one."* He picks. The choice is remembered for `~/projects/firmament/` and subtrees. He runs `python3` again from a different cwd; the dispatcher prompts again because the scope is per-cwd-prefix, not global.

That is the success criterion. If the implementation does that, Stage 1 is done. If it does not, Stage 1 is not done — no matter what intermediate scaffolding got built.

## what this forces in / out

The story forces **multi-candidate UX into Stage 1**, not Stage 3. The original plan had it punted; the story shows that punting it produces a dispatcher that silently picks the wrong python3 the first time the user has two installed, which is the most common case on a real machine. So it lands together.

The story also forces **scope-per-cwd-prefix** to be a Stage 1 concern, because "remember the answer" is in the story. The shape ports from `meta_finder._scope` (a dict of `{binary_name: (snapshot, abspath)}` keyed by some context). Field's context is cwd-prefix; bubble's was process-scope.

The story does **not** force the fault loop into Stage 1. If git fails because `/etc/gitconfig` is missing or because `git-remote-https` isn't on PATH inside the namespace, the user sees git's own error, not a field error. That's Stage 2. The honest spec for Stage 1 is: *the binary runs in the namespace and produces its own output, success or its own native failure mode.*

## scope, IN

- **`bwrap` invocation builder** — given a snapshot root + a binary path, produce an `argv` for `bwrap` that mounts the right tree.
- **Bind-mount set** — a fixed default: `<snap>/usr/lib`, `<snap>/usr/lib64`, `<snap>/lib`, `<snap>/lib64`, `<snap>/usr/share`, `<snap>/etc`, `<snap>/usr/libexec`. Shared from host: `/home`, `/tmp`, `/proc`, `/sys`, `/dev`, `/root` (or current `$HOME`). Read-only by default for the snapshot binds; rw for the host shares.
- **Multi-candidate prompt** — when the index has >1 candidate (after content-hash dedup), interactive prompt with snapshot name + last-seen-context (from lineage). Pick one.
- **Scope memory** — `~/.field/scopes.toml`, keyed by `(name, cwd-prefix-glob)`. After a prompt, write a row. Subsequent invocations whose `cwd` is under the prefix skip the prompt.
- **Lineage extension** — record `substrate=bwrap` for these dispatches, plus the namespace argv that ran (or a hash of it).

## scope, OUT

- **Fault loop / closure expansion** — Stage 2. Failures inside the namespace are returned to the user as the binary's own exit code/stderr, not retried.
- **Closure observation via strace** — Stage 4.
- **Bundle / projection** — Stage 5.
- **Pid-namespace, user-namespace, network isolation** — Stage 1 shares all of these with the host. The only namespace it creates is mount.
- **Selective bind-mount per-binary** — Stage 1 mounts the *whole* snapshot library tree because computing the per-binary subset is exactly what Stage 4 is for. Cost: the running binary sees the snapshot's full /usr/lib, even libs it doesn't need. Tradeoff accepted for Stage 1.

## what counts as evidence the story is satisfied

A 5-step manual test, in this order:

1. `field index /root/kali-fs --name kali-fs` (already passes)
2. From a fresh shell in any cwd: `git status` — produces git output, exit 0 if in a repo, lineage row records `substrate=bwrap`, no field message visible to the user.
3. `field index /some/other/snapshot --name another` to introduce a second candidate for `python3`.
4. From `/root/somewhere/`: `python3 --version` — prompt fires once, choice recorded.
5. `python3 --version` again from same subtree — no prompt, dispatch directly. From a *different* subtree — prompt fires again.

If all five hold, Stage 1 is shipped. If any fails, Stage 1 is not shipped.

## the question this story answers, and the one it doesn't

**Answers**: "Does the dispatcher produce the transparent-on-miss UX promised in the README, on the common case (dynamic binaries, often multi-candidate)?"

**Does not answer**: "Does the dispatched binary actually work for everything it would have done on the original system?" That's the closure question, and the story explicitly punts it to Stage 2-4. A binary that needs a config file we didn't bind-mount, or a daemon we didn't bring forward, fails inside the namespace on Stage 1 and the user sees the binary's own error. That's the honest scope.
