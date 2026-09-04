# Personal Cloud: Syncthing, LiveSync and Backup

Replaces iCloud Drive across an iPhone, a Windows machine, a Mac and the Linux Mint MacBook.
The Mint machine is the always-on hub; every other device is intermittent. All four are on a
Tailscale tailnet, and Syncthing is configured to use tailnet addresses only.

Three workloads, three different mechanisms, because one mechanism does not fit all three.
The reasoning for that split is in the Decisions section.

## Architecture

```mermaid
flowchart TD
  subgraph hub[Linux Mint MacBook: always on]
    st[Syncthing daemon]
    couch[CouchDB container]
    backup[Backup timer]
  end

  mac[Mac] -->|tailnet tcp 22000| st
  win[Windows] -->|tailnet tcp 22000| st
  ios[iPhone: Synctrain] -->|tailnet tcp 22000, opportunistic| st

  st --> vault0[Vault: KeePass]
  st --> docs[Documents and media]
  st --> photos[Camera roll: receive only]

  obsmac[Obsidian Mac] -->|LiveSync replication| couch
  obsios[Obsidian iOS] -->|LiveSync replication| couch
  obswin[Obsidian Windows] -->|LiveSync replication| couch

  vault0 --> backup
  docs --> backup
  photos --> backup
  couch --> backup
  backup --> ext[(External drive: versioned)]
```

| Layer | Mechanism | Why not the others |
|---|---|---|
| KeePass database | Syncthing, all four devices | Small, infrequent writes, and conflicts are recoverable by merge |
| Documents and own media | Syncthing, all four devices | Ordinary files, no sandbox problem |
| Camera roll | Syncthing, iPhone send-only | iOS will not expose the photo library as a filesystem, so this is a backup rather than a sync |
| Obsidian vault | Self-hosted LiveSync over CouchDB | Syncthing on iOS destroys vaults. See Decisions |
| External backup | Versioned copy from the hub | Syncthing replicates deletions, so it is not a backup |

***

## Decisions

### The hub is not optional

Three of four devices are intermittent, and two intermittent peers rarely have overlapping
wake windows. Syncthing's own guidance is to keep at least one device continuously running.
The Mint machine already runs clamshell with `logind` ignoring lid close, so it qualifies.

Set the hub as **introducer** so it distributes folder config to new devices. Make that
one-directional. Two devices introducing each other is explicitly discouraged upstream.

Introducer status does not force traffic through the hub. Peers still connect directly when
both happen to be awake, so the hub buys convergence rather than a bottleneck.

### Tailscale addressing, not Syncthing discovery

Syncthing's tunnelling documentation describes disabling discovery and relaying in favour of
explicit peer addresses. Applied to a tailnet:

- **Off:** global discovery, local discovery, relaying, NAT traversal
- **Peer address:** `tcp://100.x.y.z:22000` per device

This removes public relay dependence, NAT traversal flakiness, and metadata sent to
`relays.syncthing.net`. Double encryption over WireGuard is negligible for this workload.

**Force TCP on iOS.** MobiusSync issue #238 records an iPhone losing VPN-only reachability
after an iOS update, fixed by an explicit `tcp://` address. QUIC was the cause.

No VPN conflict exists. iOS permits one active tunnel, and Tailscale is the only claimant:
the Syncthing clients are ordinary apps rather than NetworkExtensions.

### Synctrain, not Möbius Sync

| | Synctrain (`pixelspark/sushitrain`) | Möbius Sync |
|---|---|---|
| Stars | 2,025 | 264 |
| Open issues | **0** | 50 |
| License | MPL-2.0 | none |
| Last code push | 2026-08-23 | **2021-03-19** |
| Syncthing version | v2 | v1.x, [#259](https://github.com/MobiusSync/MobiusSync/issues/259) open since Sep 2025 |
| Cost | Free | Free to 20MB, then one-time IAP |

Möbius still works and its author answered a 2026 "still maintained?" thread, but a code
repo untouched since 2021 against a fully open, zero-issue, actively released alternative is
not a close call.

### iOS syncs opportunistically, not continuously

No iOS app runs indefinitely in the background. Möbius' own FAQ says to expect **1 to 2 hours
of sync activity per day** once stable, and possibly 24 hours before the first sync starts.
Community reports describe background sync stopping after a reboot until the app is opened
manually.

Design consequence: **the iPhone is a device you open the app on.** Anything time-critical
travelling to or from the phone needs a manual foreground open, not a promise of background
convergence.

### Obsidian on iOS uses LiveSync, not Syncthing

This is the one hard exclusion.

Obsidian iOS cannot open an existing folder, so a Syncthing vault requires pointing at an
*external folder* through a security-scoped bookmark. Möbius labels that feature **dangerous
with a chance of data loss**, and [issue #263](https://github.com/MobiusSync/MobiusSync/issues/263)
is two independent reporters losing **every file across every machine** after a phone woke
from idle. The suspected cause is a stale bookmark presenting an empty folder, whose deletions
then replicate everywhere.

`vrtmrz/obsidian-livesync` (12.2k stars, MIT, actively pushed) is a real Obsidian plugin. It
replicates through CouchDB, runs natively on Obsidian iOS with no sandbox workaround, and
merges at document level instead of producing whole-file conflicts.

`athNdev/obsidian-sync` is Docker Compose for exactly this shape, CouchDB plus a Tailscale
sidecar plus a backup rotation script. At 4 stars, no license and last pushed 2026-02-08 it
is one person's setup. **Read it for the compose file, do not depend on it.**

### Photos are a backup, and the library does not fit

iOS does not expose the camera roll as a filesystem, so both iOS clients implement custom
Photos code and both are **send only**. Camera roll pushes to the hub; nothing returns. There
is no two-way library, no shared albums, and no delete-on-one-device semantics.

**Deletions propagate by default.** Deleting a photo on the phone removes it from the hub
unless `ignoreDelete` is set on that folder. For an archive folder, set it.

> [!warning] Disk space decides the scope
> The hub is a 2015 MacBook whose SSD will not hold a full photo library alongside documents,
> media and the vault. **Phase 1 therefore syncs files only:** documents, music, and own
> images and videos that already exist as files. The iPhone camera roll stays on iCloud.
>
> Revisit when the SSD is upgraded. The camera-roll folder is designed but not enabled, so
> enabling it later is a config change rather than a redesign.

### Syncthing is not backup

It replicates deletions, which is the #263 failure mode. The external-drive leg is what makes
that recoverable, so it is part of the design rather than an addition to it.

***

## Folder design

| Folder | Devices | Hub type | Notes |
|---|---|---|---|
| `vault` | all four | Send and receive | KeePass `.kdbx`. Small, syncs fast |
| `documents` | all four | Send and receive | Papers, scans, reference |
| `media` | Mint, Mac, Windows | Send and receive | Music, own images and videos already stored as files. **Excluded from iPhone** on space grounds |
| `camera` | iPhone, Mint | **Receive only** on hub | Designed, disabled in phase 1. Set `ignoreDelete` when enabled |

**File versioning on the hub:** staggered versioning on `vault` and `documents`. This is the
local undo for an accidental delete before the nightly backup runs.

### Ignore patterns

`documents` and `media`:

```
(?d).DS_Store
(?d)Thumbs.db
(?d).Trash-*
```

The `(?d)` prefix lets Syncthing delete these locally when they disappear remotely.

***

## KeePass discipline

Conflicts are **recoverable, not catastrophic**. Syncthing renames the losing copy to
`<file>.sync-conflict-<date>-<time>-<device>.kdbx` and propagates it as an ordinary file, so
both versions survive on disk. Nothing is silently lost.

KeePassXC resolves this directly: **Database → Merge From Database** compares entries by UUID
and modification time, keeps the newest, and pushes the previous version into entry history.
Its documentation names conflict files from cloud sync as the intended use case.
`keepassxc-cli merge` does the same for scripting.

Two settings that matter:

1. **Safe saves on.** Atomic write to a temp file then move. The default. Never use
   direct-write saves, which is the path to a truncated `.kdbx` mid-sync.
2. **Pre-save backup on.** KeePassXC's own belt and braces.

Single-writer discipline is advisable rather than mandatory. Closing the database on one
device before editing on another avoids the conflict entirely, but forgetting costs a merge
rather than data.

**Clients:** KeePassXC on Mint, Mac and Windows. Strongbox or KeePassium on iOS, both actively
maintained.

***

## Obsidian: Self-hosted LiveSync

Runs on the hub, reachable over the tailnet only.

| Piece | Detail |
|---|---|
| Server | CouchDB in a container on the Mint hub |
| Exposure | Tailnet address only. No port forward, no public certificate |
| Client | Self-hosted LiveSync plugin on Mac, Windows, iPhone |
| Merge model | Document-level, not whole-file |
| Backup | CouchDB data directory included in the nightly external-drive run |

This replaces the vault's current iCloud path on every device including iOS, which is the part
Syncthing cannot do safely.

Note the vault also has a git remote. Git stays as the reviewable history and the agent branch
boundary; LiveSync handles live multi-device editing. They serve different purposes and do not
conflict, provided `.git` is excluded from LiveSync replication.

***

## Backup to the external drive

The hub is the only backup source, since it holds every folder and is always on.

**Requirement: versioned, not mirrored.** Syncthing propagates deletions within seconds, so a
mirror reproduces the exact failure it is meant to protect against.

### FreeFileSync in Update mode with versioning

Chosen for familiarity, since it already covers the current iCloud-to-external-drive run. The
configuration is what matters:

| Setting | Value | Why |
|---|---|---|
| Variant | **Update**, not Mirror | Mirror deletes on the target whatever vanished on the source. That is the whole problem |
| Deletion handling | **Versioning**, not "Permanent" or "Recycle bin" | Moves replaced and deleted files into a versioning folder instead of removing them |
| Naming convention | **Time stamp (file)** | Keeps every revision. "Replace" keeps only the newest, which defeats the purpose |
| Versioning limits | Keep a generous minimum | The point is surviving a deletion you notice weeks later |

> [!warning] Mirror mode is the trap
> FreeFileSync defaults to a two-way or mirror mindset. Mirror plus a propagated Syncthing
> deletion equals a lost file on both source and backup. Verify the variant reads **Update**
> and the deletion handling reads **Versioning** before the first real run.

Save the configuration as a `.ffs_batch` file so it can run unattended. Set it to ignore
errors rather than pop a dialog, since nothing will be watching.

**Alternatives, if FreeFileSync proves awkward to schedule headless:** `restic` (deduplicating,
encrypted, snapshot-based, prunable) or `rsync --backup-dir` (no new dependency). Both are
strictly better for scripted use, and neither is worth the switch unless FreeFileSync fights
the timer.

### Scheduling

Runs from a **systemd user timer with `Persistent=true`** so a missed window fires on the next
boot rather than being skipped, matching how the vault automation is scheduled. Add
`loginctl enable-linger` so the unit survives logout.

### What to include

- All Syncthing folders
- The CouchDB data directory (the Obsidian vault's live store)
- The Syncthing config directory, since device IDs and folder keys are painful to rebuild

### Verify the restore

A backup nobody has restored from is a hypothesis. Before step 7 of the rollout, pull a file
out of the versioning folder and confirm it opens. Repeat after any change to the folder set.

***

## Managed files

Following the pattern in `linux-mint-macbook.md`, once implemented:

```text
~/.config/systemd/user/personal-backup.service  -> os/linux/home/systemd/personal-backup.service
~/.config/systemd/user/personal-backup.timer    -> os/linux/home/systemd/personal-backup.timer
~/.local/bin/personal-backup                    -> os/linux/home/personal-backup.sh
```

Syncthing's own config is **not** managed here. It contains device IDs and folder keys, it is
machine-specific, and Syncthing rewrites it at runtime. Back it up rather than symlink it.

A guarded `restoration_scripts/` entry follows the same shape as `01-linux-mint-macbook.sh`:
skip unless Linux, not WSL, `linuxmint`, `MacBookPro12,1`.

***

## Rollout order

Each step is independently useful and independently reversible.

| Step | Action | Why this order |
|---|---|---|
| 1 | Syncthing on the hub, Mac and Windows. `vault` folder only | Smallest surface, highest value, and the KeePass merge path gets exercised early |
| 2 | Tailscale-only addressing. Discovery and relays off, static `tcp://` peers | Do this before adding data, so problems surface on one small folder |
| 3 | Add `documents` and `media` | Bulk transfer, desktops only |
| 4 | Synctrain on the iPhone. `vault` and `documents` | Expect opportunistic sync. Verify before trusting it |
| 5 | Backup timer to the external drive | **Before** the vault moves off iCloud, so nothing is ever unprotected |
| 6 | LiveSync: CouchDB on the hub, plugin on all clients | The Obsidian half, including iOS |
| 7 | Turn off iCloud Drive for the migrated folders | Last, and only after step 5 has produced a verified restore |

> [!danger] Do not skip step 5
> Between leaving iCloud and having a working versioned backup, a single propagated deletion
> is unrecoverable. Verify a restore from the external drive before disabling iCloud, not
> after.

**Deferred:** camera roll (disk space), and any attempt to put the Obsidian vault on iOS
Syncthing (data loss).

***

## Open questions

- Whether `media` stays desktop-only permanently or the SSD upgrade brings the phone in
- Whether FreeFileSync schedules cleanly headless from a systemd timer, or whether the backup
  leg moves to restic
- Whether CouchDB runs under Docker or Podman on the hub
- Whether the Black Vault keeps its git remote as the source of truth with LiveSync as a live
  layer on top, or whether one supersedes the other for day-to-day editing
