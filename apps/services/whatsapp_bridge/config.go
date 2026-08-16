package main

import (
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the bridge's runtime configuration, all from the environment so the
// same binary runs in dev and on the VPS with no code changes.
type Config struct {
	// Addr is the host:port the bridge's own HTTP API listens on. The gateway's
	// BridgeProvider + connect routes talk to this. Default ":8790".
	Addr string
	// GatewayURL is the Metorite gateway base URL the bridge posts inbound
	// messages + pairing events to (…/whatsapp/bridge/ingest and …/paired).
	GatewayURL string
	// Secret is the shared secret sent as X-Bridge-Secret in BOTH directions.
	// Empty disables auth (local dev only) — set it in production.
	Secret string
	// StorePath is the sqlite file holding whatsmeow device sessions + our
	// account↔jid and media-reference tables. One file serves every number.
	StorePath string
	// FullHistory requests the larger on-link history sync (~1 year, desktop
	// profile) instead of the default ~3-month "recent" window. Default true.
	FullHistory bool
	// FullHistoryDays caps the requested history window (server ceiling ≈ 3 years).
	FullHistoryDays uint32
	// CallRecordDir is where each voice call's decoded peer audio is written as
	// a 16 kHz mono WAV — the raw material the note-taker pipeline transcribes.
	// Empty disables recording entirely (calls still connect).
	CallRecordDir string
	// CallReapAfter is how long an ended call stays queryable before it's
	// dropped from the in-memory registry.
	CallReapAfter time.Duration
	// CallRetentionDays bounds how long recorded call audio stays on disk.
	// Recording runs at ~115 MB per hour of call, so an unbounded directory
	// fills a VPS quietly; 0 keeps recordings forever (opt in deliberately).
	CallRetentionDays uint32
}

func envBool(key string, def bool) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	if v == "" {
		return def
	}
	return v == "1" || v == "true" || v == "yes" || v == "on"
}

func envUint(key string, def uint32) uint32 {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		return def
	}
	n, err := strconv.ParseUint(v, 10, 32)
	if err != nil {
		return def
	}
	return uint32(n)
}

func env(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

// LoadConfig reads the bridge configuration from the environment.
func LoadConfig() Config {
	return Config{
		Addr:            env("WHATSAPP_BRIDGE_ADDR", ":8790"),
		GatewayURL:      strings.TrimRight(env("WHATSAPP_BRIDGE_GATEWAY_URL", "http://localhost:8000"), "/"),
		Secret:          env("WHATSAPP_BRIDGE_SECRET", ""),
		StorePath:       env("WHATSAPP_BRIDGE_STORE", "./bridge-store.db"),
		FullHistory:     envBool("WHATSAPP_BRIDGE_FULL_HISTORY", true),
		FullHistoryDays: envUint("WHATSAPP_BRIDGE_FULL_HISTORY_DAYS", 365),
		CallRecordDir:   env("WHATSAPP_BRIDGE_CALL_RECORD_DIR", "./call-recordings"),
		CallReapAfter:   time.Duration(envUint("WHATSAPP_BRIDGE_CALL_REAP_MINS", 60)) * time.Minute,

		CallRetentionDays: envUint("WHATSAPP_BRIDGE_CALL_RETENTION_DAYS", 7),
	}
}
