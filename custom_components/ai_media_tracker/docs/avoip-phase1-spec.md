# AV-over-IP — Phase 1 Proof of Concept

**Goal:** spend the minimum money required to prove that used Just Add Power hardware
can do everything the full system needs, and that we can drive it from Home Assistant
with our own code. Phase 1 ends with a go/no-go decision, not a finished product.

**Architecture chosen:** Single-VLAN multicast switching. Every decoder holds a channel
number; routing a zone means telling that decoder to change channel. The switch is dumb
transport — we never reconfigure it to switch video. This is what makes a custom control
layer viable.

---

## 1. What Phase 1 must prove

Five things, in descending order of "this kills the project if it fails":

| # | Claim under test | Why it's risky |
|---|---|---|
| 1 | Lossless multichannel audio (ideally Atmos) survives TX→RX | Marketing says "all audio formats"; nobody's tested *your* AVR |
| 2 | HDCP 2.2 handshake works with Apple TV 4K → TX → RX → your TV | HDCP is the #1 cause of black screens in AVoIP |
| 3 | The `channel` API switches a live route, driven from the HA box | The entire control layer depends on this |
| 4 | Used units can be brought to a common firmware branch | Mixed-firmware fleets do not interoperate (see §7) |
| 5 | 4K30 4:4:4 output is subjectively acceptable | Hard ceiling on 3G/Ultra hardware (see §2) |
| 6 | Image Pull returns real frames under HDCP | Decides whether the AI pipeline keeps separate capture hardware (see §9) |

---

## 2. Hard specs (confirmed from vendor docs)

- **Video ceiling:** accepts up to 4096x2160p60 input, **outputs 4K30 4:4:4 with HDR**.
  4K60 requires the MaxColor series, which is a different (dealer-priced) product line.
- **HDCP 2.2:** supported on the 4K models. Explicitly *not* supported on 2G models
  or the 1080p "2G/3G" models.
- **Audio:** all formats including lossless / Dolby Atmos; uncompressed multichannel.
- **Bandwidth:** 180–500 Mbps per stream, transmitters burst to ~600 Mbps. Gigabit per
  endpoint; plan uplinks at roughly one stream per gigabit.
- **PoE:** 10 W max per device.
- **Control:** Telnet TCP 23, HTTP TCP 80, plus CEC-over-IP, RS232, IR.
- **Factory default IP:** 169.254.x.x link-local, self-assigning (no two units collide).

---

## 3. Buy list

### Get (all "Ultra Series" = the 4K line; `VBS-HDIP-` and `UL-` prefixes are the same hardware renamed)

| Qty | Model | Role | Used target |
|---|---|---|---|
| 2 | `UL-707POE` / `VBS-HDIP-707POE` | Encoder (transmitter) | $120–200 ea |
| 2 | `UL-508POE` / `VBS-HDIP-508POE` | Decoder (receiver) | $100–175 ea |
| 1 | Managed PoE switch — see §4 | Transport | $60–150 |

`UL-708POE` is the 707POE plus stereo audio extraction — worth a small premium if the
price is close, useless if you're not feeding a stereo zone amp. `UL-518AVP` / `UL-718AVP`
are the "A/V Pro" units that can distribute stereo *and* 5.1 simultaneously via onboard
DSP — the upgrade path if test #1 disappoints. `UL-509POE` is the daisy-chain receiver —
buy this variant for any source-bearing room with only one network drop (see §6).

**Budget: roughly $550–900 all-in.**

> Change from my earlier estimate: I originally floated ~$300 for 1 TX + 2 RX. That's wrong
> for a matrix proof — with one source you cannot test switching between two live sources,
> which is the single most important behavior. Two of each is the real minimum. If budget
> forces it, 1 TX + 2 RX still proves audio, HDCP and distribution, but defers the switching
> test, which is the one I'd least want to defer.

### Avoid on eBay

- **Anything 2G.** Firmware B3.x explicitly does not support 2G devices.
- **The 1080p models** — `505POE`, `515POE` (receivers) and `705POE`, `715POE`, `716VGA`,
  `725POE`, `726TVI` (transmitters). These are 1080p-max with no HDCP 2.2. They look
  superficially like the 4K units and are frequently mislisted as "4K" by sellers.
- **Discontinued oddballs** unless cheap and you know why you want them: `717HIFI`,
  `718KVM`, `749KVM`, `767DSS`.
- **Listings that won't state firmware version.** Ask before buying (see §7).

Mnemonic: **5-0-8 and 7-0-7 and up are 4K. 5-0-5 / 7-0-5 / x15 are 1080p.**

---

## 4. Switch requirements

Must support, all four: **IGMP snooping**, **IGMP querier**, **jumbo frames (MTU ≥ 9000)**,
and **PoE**. Managed, gigabit. PoE budget for Phase 1 is trivial (4 × 10 W = 40 W).

If you intend to daisy-chain two J+P units onto one drop (§6), those ports must be
**802.3at / PoE+ (30 W)**, not just 802.3af. Check the per-port standard and the switch's
total PoE budget before buying — this is the one spec cheap PoE switches most often miss.

Recommended, cheapest first:

1. **Cisco SG350-10P** (used, ~$80–120) — the Cisco SG family is what J+P officially
   documents and tests against. Safest bet for a proof of concept.
2. **TP-Link TL-SG2210P** (~$100 new) — meets all four requirements, easy web UI.
3. **Ubiquiti USW-Pro-24-PoE** — only if you're already in UniFi; J+P publishes an
   official UniFi guide.

### Configuration

```
VLAN 101 "AV"            all four J+P ports, untagged/native, dedicated to J+P only
IGMP snooping            ENABLED on VLAN 101
IGMP fast/immediate leave ENABLED
IGMP querier             ENABLED — exactly ONE per system
Multicast filtering      ENABLED (disable multicast flooding)
Permitted multicast      239.92.0.0 – 239.99.99.99
Jumbo frames             ENABLED, MTU 9000+, on all J+P ports and trunks
Inter-VLAN routing       ENABLED to/from VLAN 101 (control traffic only)
```

> ⚠️ **Landmine.** The 2019-era *3G Project Planning Guide* and a lot of forum advice say
> "IGMP snooping **disabled**, one VLAN per transmitter." That describes the **legacy
> JADConfig VLAN-switching method**, where routing meant reconfiguring the switch. We are
> deliberately not doing that — it's the opposite of what we need, and JADConfig reaches
> **end of support on 1 January 2027**. For multicast switching, IGMP snooping is **on**.

---

## 5. Network plan

Video multicast never leaves VLAN 101 (J+P video will not traverse a routed network).
Control traffic is ordinary unicast TCP and routes fine — that's how HA reaches it.

| Device | IP | Channel |
|---|---|---|
| VLAN 101 gateway (switch SVI) | 192.0.2.1 | — |
| Encoder A (rack, e.g. Apple TV) | 192.0.2.11 | 11 |
| Encoder B (rack, second source) | 192.0.2.12 | 12 |
| Decoder 1 (Room A) | 192.0.2.51 | — |
| Decoder 2 (Room B) | 192.0.2.52 | — |

On the LAN router: static route `192.0.2.0/24` → switch uplink IP. Allow **TCP 80**
and **TCP 23** from `198.51.100.0/24` → `192.0.2.0/24`. Home Assistant sits at
`198.51.100.127` and needs no interface on the AV VLAN.

---

## 6. Wiring — distributed sources

**Topology reality:** sources are not in a central rack. Each source lives in a room next
to the TV it came with. That means a source-bearing room needs **both** an encoder (so the
rest of the house can watch its source) and a decoder (so it can watch everyone else's).

```
ROOM A                                   ROOM B
[Apple TV] --HDMI--> [Encoder A]         [source] --HDMI--> [Encoder B]
[AVR] --> [TV]  <--HDMI-- [Decoder 1]    [TV]  <--HDMI-- [Decoder 2]
     |                                        |
     └────── Cat6 ──┐              ┌── Cat6 ──┘
                    │              │
              [PoE switch, VLAN 101] ── uplink ──> LAN / HA
```

Pick two rooms that each have a source, and at least one with the AVR — test T1 needs a
real surround decoder to report the incoming format. Run Decoder 1 → AVR → TV so the AVR
can display the audio format.

The room's TV watches its own local source *through* the matrix rather than over direct
HDMI. One TV input, one control path, consistent behavior. Costs a frame of latency and
saves a great deal of complexity.

### Per-room economics (this is the real budget driver)

| Room type | Needs | Used cost |
|---|---|---|
| Source **and** TV | 1 encoder + 1 decoder | $220–375 |
| TV only | 1 decoder | $100–175 |
| Source only (rare) | 1 encoder | $120–200 |

Phase 1's 2 encoders + 2 decoders is exactly two source-bearing rooms, so the kit in §3 is
unchanged — but whole-house cost is roughly double a rack-based assumption. Worth knowing
before you extrapolate.

### Cabling: two Cat6a per TV, one already occupied

This is the actual situation here, and it maps onto the design better than it first looks —
because **the two drops correspond to the two VLANs**:

```
drop 1  ──> AV VLAN 101 ──> [509POE decoder] ──pass-through + PoE──> [707POE encoder]
                                   │                                        │
                                to TV                                  to Apple TV

drop 2  ──> data VLAN ──> unchanged: whatever is on it today
```

Both J+P units share drop 1 via the daisy-chain receiver's pass-through port; drop 2 keeps
doing its current job untouched. Nothing gets displaced and no new cable gets pulled.

**Do not put the Apple TV on the pass-through port.** It bridges onto AV VLAN 101, which
has no DHCP and no internet route by design (§4/§5). Source devices belong on drop 2.

The daisy chain only yields *independent* zones **because we chose multicast switching**.
J+P documents that under VLAN switching a daisy-chained device is forced to the same source
as its parent, whereas "if you are using IGMP switching then you can set different sources
on each display in the chain." Another dividend from §4.

⚠️ **PoE budget — affects switch choice.** Each J+P device draws up to 10 W. Two units on
one drop needs ~20 W at that port, which exceeds 802.3af (12.95 W delivered). Daisy-chained
rooms require **802.3at / PoE+ (30 W)** ports upstream. Many budget PoE switches are af-only
or offer at on just a couple of ports — check before buying, and revisit §4 if you plan to
daisy-chain more than one or two rooms. *(The 509POE's exact pass-through wattage isn't in
J+P's public spec sheet — confirm on the unit before committing to a whole-house pattern.)*

**Sourcing note:** the 509POE is **discontinued**. Fine for a used-gear plan, but supply is
thinner than the 508POE, so don't build a whole-house design that depends on finding six.

### Sonos Arc rooms

Sonos surround bonding does need a wire, but only one — and it's the **Arc**, not the Amp.
Sonos's app requires the Arc to be hardwired to the router when an Amp is used as surrounds;
the modern Amp itself bonds to the Arc over a private 5 GHz link and needs no Ethernet.
(That's specific to the current Amp — the older Connect:Amp did require wiring.)

So a Sonos room needs exactly one wired data device more than a plain room, which drop 2
already covers. Where drop 2 has to serve several devices — Arc, Apple TV, TV — put a cheap
**5-port unmanaged gigabit switch** on it. Data-VLAN traffic needs none of §4's tuning, so a
$20 switch is genuinely fine here. Only the J+P gear needs the managed switch.

Keep **all** Sonos devices on the data VLAN together. Sonos requires its devices on one
subnet and leans on mDNS/multicast; splitting them across VLANs breaks bonding. Nothing
about the Sonos setup needs to change — leave it exactly as it works today.

```
drop 1 ──> AV VLAN ──> [509POE decoder] ──pass-through──> [707POE encoder]
drop 2 ──> data VLAN ──> [$20 unmanaged switch] ──> Sonos Arc (wired)
                                                └──> Apple TV
                                                └──> TV
                                          Sonos Amp ── wireless to Arc
```

### The cheaper alternative: skip the daisy chain

If 509POEs prove scarce or PoE+ is inconvenient, use both drops for J+P — drop 1 to the
decoder, drop 2 to the encoder, both on AV VLAN 101 — and move the displaced device to
Wi-Fi. An Apple TV needs ~25 Mbps for 4K, which Wi-Fi handles comfortably. Costs nothing,
uses plain 508POEs, standard 802.3af.

Worth asking what's actually on drop 2 first. Once every TV gets its content *through the
matrix*, you stop using the smart TV's built-in apps — so the TV's own network connection
may simply stop mattering, and the drop frees itself.

### ⚠️ Mixed audio capability — the EDID constraint

Having Arcs in *some* rooms is the most consequential thing about this topology, and it is
not a J+P quirk — it's inherent to any one-source-to-many-displays HDMI system.

The audio path is unchanged by the matrix. The Arc has only an eARC port, so it can't be
fed from a decoder directly; audio still goes decoder → TV → eARC → Arc, exactly as it goes
direct-HDMI → TV → eARC → Arc today. Good news: if Atmos works in that room now, the only
new variable is whether J+P delivers the bitstream to the TV intact.

The hard part is upstream. **An encoder presents one EDID, so its source emits one audio
format — for every room watching it simultaneously.** HDMI carries a single format at a
time. Set the Apple TV's encoder to advertise Atmos and a plain stereo TV watching the same
source may get silence; set it to stereo and the Arc room loses Atmos. The source has no way
to know who's listening.

J+P's tools for this, cheapest first:

1. **Lowest-common-denominator EDID.** Encoders carry built-in EDID profiles (plus capture
   and custom hex entry). Pick a format every zone handles — PCM 2.0 or DD 5.1. Everything
   works everywhere; nobody gets Atmos. Start here.
2. **`UL-718AVP` / `UL-518AVP` A/V Pro units.** The onboard Dolby Digital Sound Processor
   emits stereo *and* 5.1 simultaneously, which is precisely this problem's designed
   solution. Note the documented transform is DD 5.1 → Pro Logic II; whether it also passes
   Atmos alongside stereo is **not documented** — verify before paying the premium.
3. **Dynamic EDID (ours to build).** Because we control routing, HA knows exactly which
   rooms are on which channel. It can set an encoder's EDID to Atmos while only the Arc room
   is watching, and drop to a safe profile the moment a stereo room joins. Off-the-shelf
   drivers don't do this. It costs an HDMI re-negotiation (a brief video drop) on change, so
   it's a Phase 3 idea, not a Phase 1 assumption — but it's the payoff for owning the
   control layer.

Practical stance for Phase 1: configure a safe EDID, confirm everything has audio
everywhere, and treat Atmos in the Arc rooms as the stretch goal measured by T1/T1b.

---

## 7. Bring-up order

Firmware first. Do not skip this.

1. **Inventory firmware.** Each unit has a web page — browse to its IP for version.
   Fresh out of the box a unit is at a self-assigned `169.254.x.x`; set a laptop NIC to
   `169.254.0.9 / 255.255.0.0` and connect point-to-point to find it.
2. **Get every unit onto the same firmware branch.** `B3.x.x` does **not** interoperate
   with `B2.x.x` or `B1.x.x`. A fleet split across branches will fail in confusing ways.
   Target the latest Omega/Ultra `B3.x`. This is the #1 risk of buying used.
3. **Factory reset** anything that came out of a previous commercial install — it may
   carry a JADConfig static IP scheme and VLAN assumptions.
4. **Set static IPs** per §5, via telnet (port 23) or the web UI:
   ```
   astparam s ip_mode static
   astparam s ipaddr 192.0.2.11
   astparam s netmask 255.255.255.0
   astparam s gatewayip 192.0.2.1
   astparam save
   reboot
   ```
   *(Verify exact syntax against J+P KB article 310 — commands are case-sensitive and
   multiple commands can be chained with `;`.)*
5. **Assign encoder channels** (11 and 12), then point each decoder at a channel.

---

## 8. Acceptance tests

Run in order. Each has an unambiguous pass condition.

**T1 — Audio passthrough (highest risk).**
Play a Dolby Atmos title on the Apple TV through Encoder A → Decoder 1 → TV → eARC → Arc.
Set the encoder EDID to an Atmos-capable profile for this test.
✅ **Pass:** the Sonos app's now-playing screen reports `Dolby Atmos` (or at minimum
`Dolby Digital Plus` / `Dolby TrueHD`). An AVR room should show the same on its front panel.
❌ **Fail:** reports `Stereo` / `PCM 2.0` → something in the chain is downmixing.
Isolate before concluding it's J+P: check the TV's eARC passthrough setting first, since the
TV sits between the decoder and the Arc and is an equally likely culprit. Then retest with an
`18AVP` encoder.

**T1b — Mixed-zone audio (the constraint Sonos actually creates).**
With the Atmos EDID from T1 still set, have **both** decoders watch Encoder A at once —
Room A into the Arc, Room B into a plain TV with built-in speakers.
✅ **Pass:** both rooms have working audio simultaneously.
❌ **Fail (expected):** Room B is silent or noisy because it can't decode the bitstream.
This is the EDID problem from §6, not a hardware fault. Fall back to a safe EDID, confirm
both rooms recover, and record which profile worked — that value is the system default until
dynamic EDID exists.

**T2 — HDCP 2.2.**
Apple TV set to 4K, playing protected content (an iTunes 4K movie, not YouTube).
✅ **Pass:** picture on both decoders, no HDCP error, no intermittent black.
Set the Apple TV to "Match Content: Frame Rate" — with a 4K30 ceiling this matters.

**T3 — Switching via the API (the control-layer test).**
From the Home Assistant box, not from a laptop on the AV VLAN:
```bash
# HTTP — the call our driver will make
curl -X POST http://192.0.2.51/cgi-bin/api/command/channel \
     -H 'Content-Type: application/json' \
     -d '{"cmd":"set","channel":12}'
# expect: {"data":"ok"}

# Telnet equivalent
printf 'channel 12\r\n' | nc 192.0.2.51 23
```
✅ **Pass:** Room A flips from source A to source B in about a second, and the reverse
call flips it back, and Room B is unaffected. Verifies routing, isolation, *and* that
control works across the VLAN boundary — all three at once.

**T4 — Audio breakaway (the differentiator).**
```bash
printf 'channel -v 11\r\nchannel -a 12\r\n' | nc 192.0.2.51 23
```
✅ **Pass:** Room A shows source A's video with source B's audio. This is the primitive
that makes "Sonos for video" possible; worth knowing early whether it works.

**T5 — CEC power.**
Via CEC-over-IP from Decoder 1, power the room TV off and on.
✅ **Pass:** TV responds. Unlocks "drag a source onto a sleeping TV and it wakes."

**T6 — Subjective 4K30.**
Watch 20 minutes of real content. Navigate the Apple TV UI at 30 Hz.
✅ **Pass:** you don't notice. ❌ **Fail:** budget MaxColor for the gaming/sports room.

**T7 — Image Pull under HDCP (decides the AI pipeline — see §9).**
Enable Image Pull on Encoder A, then fetch a frame while the Apple TV plays **protected**
content (an iTunes 4K movie, not YouTube):
```bash
# on the encoder, via telnet
start_image_pull 640 1 2

# from the HA box
curl -o /tmp/pull.png http://192.0.2.11/pull.png
```
✅ **Pass:** the PNG shows the actual movie frame.
❌ **Fail:** black, blank, or a "protected content" placeholder.
Then repeat on unprotected content (YouTube, live TV) to see whether the failure is
HDCP-specific or total. Also eyeball whether a sports scoreboard is legible at 640×360.

---

## 9. Feeding the Now Playing pipeline

The AI tracker needs frames from each source. J+P changes how it gets them, and there
are three separate paths that used to be one.

**Frames for VLM analysis — Image Pull.** Every encoder and decoder serves a still frame
over plain HTTP:

| Endpoint | Series | Notes |
|---|---|---|
| `http://[device-ip]/pull.png` | 2G / Omega / **Ultra** | our hardware |
| `http://[device-ip]/pull.bmp` | 2G / Omega / **Ultra** | same, uncompressed |
| `http://[device-ip]/pull.jpg` | MaxColor only | — |
| `http://[device-ip]:8080/?action=stream` | **MaxColor only** | MJPEG motion stream |

Ultra tops out at **640×360** stills (160×90 and 320×180 also available); 720p/1080p and
the MJPEG stream are MaxColor-exclusive. Refresh is a configurable seconds-per-frame value,
recommended ≥ 1 s. Enable persistently with `astparam s pull_on_boot {SIZE}_{PRI}_{FREQ}`
then `astparam save`, or temporarily with `start_image_pull 640 1 2`. J+P warns that
"larger snapshots may affect HDMI video performance," so keep the frequency modest.

This is a good fit — the VLM analyses are frame-based already, so a periodic HTTP GET
replaces the RTSP capture and its ffmpeg transcode entirely, with no extra hardware.
**Poll the encoders, not the decoders:** one poll per *source* covers every room watching
it, and stays constant as rooms are added.

**The open question is HDCP** (test T7). J+P does not document whether Image Pull renders
protected content. The encoder is a licensed HDCP receiver and necessarily holds the
decrypted frame internally, so it *can* — but exposing it over unauthenticated HTTP is
exactly what HDCP licensing forbids, so it may well come back black. Untested either way;
don't design around a guess.

If T7 fails, keep the current arrangement in parallel for protected sources — which you
already have, given the existing `RTSP URL (Only required for Apple TVs via Encoders)`
config option:

```
[Apple TV] --HDMI--> [splitter] --> [J+P encoder]      (video to TVs)
                              \--> [existing RTSP encoder] --> HA  (frames for AI)
```

**Do not try to tap the network stream.** The J+P video signal carries hardware AES-256
encryption, so ffmpeg cannot join the multicast group and decode it. Image Pull or a
parallel capture are the only two ways in.

**Video for casting to non-TV targets.** The existing ffmpeg paths
(`/api/ai_media_tracker/video/`, `/audio/`, `/ws/`) are still required for phones, tablets,
Sonos and Chromecast — J+P can't feed those. But `follow_me` targeting an actual TV stops
needing a software stream at all: it becomes a channel change on that room's decoder.
`RoutingManager` gains a second, cheaper path rather than losing its existing one.

### The capture tap — cast-to-anything without an encoder per source

The instinct is that keeping "cast any source to any device" means one IP encoder per
source. It doesn't, because **a decoder is a source selector**. Park one spare decoder in
the rack, feed its HDMI output into a single IP encoder, and let HA drive its channel:

```
                       ┌─ ch 11 ─┐
[J+P network] ─────────┤  ch 12  ├──> [spare decoder] --HDMI--> [IP encoder] --RTSP--> HA
                       └─ ch 13 ─┘         ▲                                            │
                                           └── HA sets channel ─────────────────────────┘
```

To cast source 12 to a phone, HA sets the tap decoder to channel 12, then streams from the
encoder exactly as it does today. One tap covers **every source, present and future**.

Cost scales with **concurrent distinct sources being cast**, not with number of sources
owned, and not with number of viewers.

**A tap is not a single cast.** `RoutingManager`'s `StreamMultiplexer` already fans one
ffmpeg process out to N clients. One tap streaming source 12 can feed three phones, a
tablet and a Sonos simultaneously. The limit is one *distinct source* in flight per tap,
not one destination.

**Keep the encoders you already own.** The existing Apple-TV RTSP encoders stay exactly
where they are — hardwired, always available, no new spend, no channel contention. The tap
decoder covers the long tail of sources that have no capture hardware at all today.
Concurrency becomes *existing encoders + taps*, which is **more** than the current setup,
not less. Nothing regresses.

**Distributed sources don't constrain the taps.** A tap decoder is fed by the *network*,
not by an HDMI cable from a source, so it can live anywhere there's a drop — put all of
them in the rack beside HA and the switch. This is the point of the matrix: everything
downstream of the network stops caring where the sources physically are.

That resurrects the multi-input encoder. It was only ruled out for *per-source* encoding,
which would require four HDMI cables meeting in one room that doesn't exist. Tap decoders
genuinely do all sit in one place, and their HDMI outputs land in one chassis:

```
[network] ──> [tap decoder 1] --HDMI--┐
[network] ──> [tap decoder 2] --HDMI--┤──> [ENC4, 4x HDMI in] --RTSP--> HA
[network] ──> [tap decoder 3] --HDMI--┤        (all in the rack)
[network] ──> [tap decoder 4] --HDMI--┘
```

**Scaling, if one tap proves tight.** Taps are linear and the encoder side gets cheaper in
bulk:

| Encoder | Channels | Price | Per channel |
|---|---|---|---|
| LinkPi ENC1 / ENC1-V3 | 1 | $100–150 | $100–150 |
| LinkPi ENC1Pro (4K60) | 1 | $280–320 | $280–320 |
| LinkPi ENC4 / ENC4S | 4 | ~$300–400 | ~$75–100 |
| LinkPi ENC8 | 8 (1080p) | ~$400–500 | ~$50–60 |

So a two-tap bank is roughly two used decoders plus a cheap encoder pair; a four-tap bank
is four used decoders plus one ENC4, around $700–1100 all-in. Per-source encoders never
stop growing; taps stop the moment you have enough concurrency.

**Taps are fungible with room decoders.** A tap is an ordinary `508POE` — the same part as
a room decoder. An extra one is never stranded inventory: it becomes a room decoder the
day you add a room. That makes over-buying by one essentially free of risk, which is not
true of per-source encoders.

**Recommendation: build two taps, not one.** One is genuinely tight — two different people
wanting two different sources on two phones is an ordinary Tuesday. Two covers that, uses
the cheapest part in the system, and stays useful even if you later decide you were wrong.

Two caveats. HDCP is unchanged, not solved — whatever splitter or stripper arrangement
makes the current Apple TV capture work has to sit in the same place relative to the tap
encoder. And the tap is **entirely optional and deferrable**: nothing in Phase 1 depends on
it, and the current setup keeps working untouched during the transition.

Worth asking before paying for any of it: with hardware routing, "cast to a TV" — which was
the original reason software casting existed here — is replaced by something strictly
better. What actually remains is casting to phones, tablets and speakers. If that's rare in
practice, the honest answer may be one tap, or none.

**The quiet win:** routing state becomes ground truth. Today the pipeline infers what a
room is showing from VLM output plus native metadata. Once routing is ours, HA knows each
decoder's channel, so room → decoder → channel → encoder → source is a lookup, not a guess.
The VLM only has to identify *content*, never *which input is live*. That should measurably
cut the failure modes `pipeline/resolver.py` currently has to arbitrate.

---

## 10. Go / no-go

| Outcome | Decision |
|---|---|
| T1–T3 pass | **Go.** Start Phase 2 (`matrix_manager` driver). Buy remaining rooms opportunistically. |
| T1 fails on 508POE, passes on 518AVP | Go, revised buy list to A/V Pro receivers (higher per-room cost). |
| T2 fails | Stop. Debug EDID before spending more — this is usually fixable, never ignorable. |
| T3 fails | **Stop.** Without the API there is no custom control layer and the whole premise collapses. |
| T6 fails badly | Go for most rooms; price MaxColor for the one or two that need 4K60. |
| T7 passes | Retire the per-source RTSP encoders; AI frames come from Image Pull. |
| T7 fails on HDCP | Go, but keep splitter + RTSP encoder on protected sources (§9). Not a blocker. |

---

## 11. What Phase 2 inherits

The tested-and-known-good facts that the driver will encode:

- Route a zone = one HTTP POST to that decoder's IP. Idempotent, ~1 s, no switch involvement.
- Channel number is the stable identifier for a source. Encoder IP ↔ channel mapping is
  our config.
- Decoders are individually addressable and independent — no global lock, no chassis state.
- Control is routable unicast; HA needs no presence on the AV VLAN.
- Video and audio can be routed independently (pending T4).
- Each source serves AI frames at `http://[encoder-ip]/pull.png` (pending T7), so the
  capture path becomes an HTTP GET per source rather than an ffmpeg transcode per room.
- Knowing every decoder's channel means the pipeline can resolve room → source
  deterministically instead of inferring it.
- Knowing which rooms watch which channel is also what makes **dynamic EDID** possible
  later (§6) — the audio capability of a source can follow its actual audience.

This maps onto `managers/routing_manager.py`'s existing `active_routes` source→destination
model, and surfaces in the existing `ai-media-router-card.js` drag-and-drop UI as a second
destination type alongside the current Cast targets.
