package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// GatewayClient posts inbound messages + pairing events to the Metorite
// gateway. The JSON shapes here are the exact contract the gateway's
// parse_bridge_payload / bridge_paired handlers consume — keep them in sync with
// apps/services/gateway/gateway/routes/whatsapp/transport/bridge.py.
type GatewayClient struct {
	baseURL string
	secret  string
	http    *http.Client
}

// NewGatewayClient builds a client for the given gateway base URL + shared secret.
func NewGatewayClient(baseURL, secret string) *GatewayClient {
	return &GatewayClient{
		baseURL: baseURL,
		secret:  secret,
		http:    &http.Client{Timeout: 30 * time.Second},
	}
}

// bridgeMedia mirrors the WhatsAppMedia dataclass the gateway parser expects.
type bridgeMedia struct {
	WAMediaID string `json:"wa_media_id"`
	MimeType  string `json:"mime_type,omitempty"`
	Filename  string `json:"filename,omitempty"`
	SizeBytes int64  `json:"size_bytes,omitempty"`
}

// bridgeMessage mirrors the WhatsAppMessage dataclass fields the parser reads.
type bridgeMessage struct {
	WAMessageID       string       `json:"wa_message_id"`
	ChatID            string       `json:"chat_id"`
	Direction         string       `json:"direction"`
	Kind              string       `json:"kind"`
	SenderWAID        string       `json:"sender_wa_id"`
	SenderName        string       `json:"sender_name"`
	BodyText          string       `json:"body_text"`
	QuotedWAMessageID string       `json:"quoted_wa_message_id,omitempty"`
	Mentions          []string     `json:"mentions,omitempty"`
	Media             *bridgeMedia `json:"media,omitempty"`
	GroupSubject      string       `json:"group_subject,omitempty"` // set for group history
	ChatKind          string       `json:"chat_kind"`
	SentAt            string       `json:"sent_at,omitempty"` // RFC3339
}

// bridgeContact mirrors the WhatsAppContact dataclass.
type bridgeContact struct {
	WAID        string `json:"wa_id"`
	PhoneNumber string `json:"phone_number,omitempty"`
	Name        string `json:"name,omitempty"`
}

// ingestPayload is the body POSTed to /whatsapp/bridge/ingest. Backfill marks a
// history-sync batch: the gateway persists it but SKIPS the post-sync hooks (no
// auto-replies to months-old messages); a single Reclassify runs at the end.
type ingestPayload struct {
	AccountID string          `json:"account_id"`
	Messages  []bridgeMessage `json:"messages"`
	Contacts  []bridgeContact `json:"contacts,omitempty"`
	Backfill  bool            `json:"backfill,omitempty"`
}

// pairedPayload is the body POSTed to /whatsapp/bridge/paired.
type pairedPayload struct {
	Session     string `json:"session"`
	PhoneNumber string `json:"phone_number"`
	DisplayName string `json:"display_name"`
	JID         string `json:"jid"`
}

// bridgeLabel mirrors one native WhatsApp label/list the user created, as the
// gateway's /bridge/labels handler expects. Colour is both the raw whatsmeow
// palette index and a resolved hex so the UI can render the same swatch.
type bridgeLabel struct {
	WALabelID    string `json:"wa_label_id"`
	Name         string `json:"name"`
	Color        string `json:"color,omitempty"`     // resolved hex (#RRGGBB)
	ColorIndex   int32  `json:"color_index"`         // raw whatsmeow palette index
	ListType     string `json:"list_type,omitempty"` // CUSTOM|PREDEFINED|LEAD|…
	PredefinedID int32  `json:"predefined_id,omitempty"`
	SortOrder    int32  `json:"sort_order,omitempty"`
	Active       bool   `json:"active"`
	Deleted      bool   `json:"deleted"`
}

// bridgeLabelAssoc mirrors a chat↔label (un)labeling, keyed by the chat JID.
type bridgeLabelAssoc struct {
	WALabelID string `json:"wa_label_id"`
	ChatID    string `json:"wa_chat_id"` // the chat JID
	Labeled   bool   `json:"labeled"`
}

// labelsPayload is the body POSTed to /whatsapp/bridge/labels. Either array may
// be empty — a LabelEdit sends one label, a LabelAssociationChat sends one assoc.
type labelsPayload struct {
	AccountID    string             `json:"account_id"`
	Labels       []bridgeLabel      `json:"labels,omitempty"`
	Associations []bridgeLabelAssoc `json:"associations,omitempty"`
}

func (g *GatewayClient) post(ctx context.Context, path string, body any) error {
	buf, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost, g.baseURL+path, bytes.NewReader(buf))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if g.secret != "" {
		req.Header.Set("X-Bridge-Secret", g.secret)
	}
	resp, err := g.http.Do(req)
	if err != nil {
		return err
	}
	// Drain + close so the keep-alive connection can be reused.
	defer func() {
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
	}()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("gateway %s -> HTTP %d", path, resp.StatusCode)
	}
	return nil
}

// Ingest streams a normalized message batch for a paired account to the gateway,
// where it flows through the same persist + triage pipeline as a Cloud API number.
func (g *GatewayClient) Ingest(ctx context.Context, p ingestPayload) error {
	return g.post(ctx, "/whatsapp/bridge/ingest", p)
}

// Paired tells the gateway a QR pairing completed and carries the number's identity.
func (g *GatewayClient) Paired(ctx context.Context, p pairedPayload) error {
	return g.post(ctx, "/whatsapp/bridge/paired", p)
}

// Reclassify asks the gateway to run the chat-status classifier once over an
// account, after a history backfill has landed (backfill batches skip the hooks).
func (g *GatewayClient) Reclassify(ctx context.Context, accountID string) error {
	return g.post(ctx, "/whatsapp/bridge/reclassify",
		map[string]string{"account_id": accountID})
}

// IngestLabels streams native WhatsApp labels and/or chat associations to the
// gateway, where they are upserted into wa_labels + wa_chat_labels and mirrored
// (read-only) into the triage inbox.
func (g *GatewayClient) IngestLabels(ctx context.Context, p labelsPayload) error {
	return g.post(ctx, "/whatsapp/bridge/labels", p)
}

// bridgeAvatar mirrors one chat's fetched profile-picture URL, as the gateway's
// /bridge/avatars handler expects. avatar_url is WhatsApp's own CDN URL — the
// gateway stores the pointer, not the bytes (same posture as labels).
type bridgeAvatar struct {
	WAChatID   string `json:"wa_chat_id"`
	AvatarURL  string `json:"avatar_url"`
	AvatarHash string `json:"avatar_hash,omitempty"`
}

// avatarsPayload is the body POSTed to /whatsapp/bridge/avatars.
type avatarsPayload struct {
	AccountID string         `json:"account_id"`
	Avatars   []bridgeAvatar `json:"avatars"`
}

// IngestAvatars reports one or more freshly-fetched profile-picture URLs.
func (g *GatewayClient) IngestAvatars(ctx context.Context, p avatarsPayload) error {
	return g.post(ctx, "/whatsapp/bridge/avatars", p)
}

// CallEvent reports one voice call's current state (ringing, active, ended, …)
// so the gateway can surface it live instead of polling. The body is the
// callInfo snapshot from calls.go; the gateway treats it as advisory and always
// re-reads the bridge for authoritative state.
func (g *GatewayClient) CallEvent(ctx context.Context, info callInfo) error {
	return g.post(ctx, "/whatsapp/bridge/call-event", info)
}
