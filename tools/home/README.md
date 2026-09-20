# tools/home/ — Home automation (future module)

The second module, planned for Phase 3. Its purpose is to prove the platform is genuinely
extensible by adding a service that is fundamentally different from RAG: it needs **device
I/O** and **event handling**, not just inference over documents.

Open decisions (see [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#8-open-questions)):

- Which device protocols first — Zigbee / Z-Wave / Matter / Wi-Fi.
- Bridge an existing local stack (e.g. Home Assistant) as a module vs. build native.
- What the core's device-I/O and event-bus capability looks like.

Runs on the same `isolated-lan` subnet the platform owns, WAN-denied like every module.
