// Command whatsapp_bridge is Metorite's unofficial WhatsApp transport: it
// pairs a PERSONAL number by QR (via the whatsmeow multi-device library), holds
// the session locally, and streams normalized messages to the gateway's
// /whatsapp/bridge/ingest endpoint — where they flow through the exact same
// store + AI triage brain as an official Cloud API number.
//
// It exposes a tiny HTTP API the gateway calls (all guarded by a shared secret):
//
//	POST /session          {session}                       -> {status, qr}
//	GET  /session/{id}                                      -> {status, qr}
//	POST /send             {session,to,body,reply_to}       -> {id}
//	POST /media            {session,media_id}               -> raw bytes
//	POST /read             {session,message_id,chat,sender} -> {ok}
//	POST /call             {session,to|group_id|targets}    -> callInfo
//	POST /call/hangup      {session,call_id}                -> callInfo
//	POST /call/answer      {session,call_id}                -> callInfo
//	POST /call/reject      {session,call_id}                -> callInfo
//	GET  /calls?session=                                    -> {calls}
//	GET  /health                                            -> {ok}
//
// It holds NO gateway credentials and never talks to Meta's Graph API. NOTE:
// using an unofficial multi-device client for a personal number is outside
// WhatsApp's Terms of Service and can get the number banned — this is for
// managing a personal line at the operator's own risk, not a business number.
package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func main() {
	cfg := LoadConfig()
	logger := waLog.Stdout("bridge", "INFO", true)
	ctx := context.Background()

	store.DeviceProps.Os = strPtr("Metorite")
	// Ask the phone for a larger on-link history sync (~1 year) instead of the
	// default ~3-month recent window, so a freshly paired number backfills its
	// recent history — the same payload WhatsApp Desktop receives on link.
	if cfg.FullHistory {
		store.DeviceProps.RequireFullSync = boolPtr(true)
		if store.DeviceProps.HistorySyncConfig != nil {
			store.DeviceProps.HistorySyncConfig.FullSyncDaysLimit = uint32Ptr(cfg.FullHistoryDays)
		}
		logger.Infof("full history sync enabled (~%d days requested on link)", cfg.FullHistoryDays)
	}

	container, err := sqlstore.New(
		ctx, "sqlite",
		"file:"+cfg.StorePath+"?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)",
		logger.Sub("db"))
	if err != nil {
		logger.Errorf("open whatsmeow store: %v", err)
		os.Exit(1)
	}

	meta, err := OpenMetaStore(ctx, cfg.StorePath+".meta")
	if err != nil {
		logger.Errorf("open meta store: %v", err)
		os.Exit(1)
	}
	defer meta.Close()

	gw := NewGatewayClient(cfg.GatewayURL, cfg.Secret)
	mgr := NewSessionManager(container, meta, gw, logger, cfg.CallRecordDir)
	mgr.RestoreSessions(ctx)
	// Sweep once at boot so a restart reclaims disk even if the process never
	// stayed up long enough for the periodic pass to fire.
	mgr.calls.sweepRecordings(cfg.CallRetentionDays)
	go reapCalls(mgr, cfg.CallReapAfter, cfg.CallRetentionDays)

	srv := &Server{cfg: cfg, mgr: mgr, log: logger}
	httpSrv := &http.Server{Addr: cfg.Addr, Handler: srv.routes(), ReadHeaderTimeout: 10 * time.Second}

	// A listener failure signals the main goroutine (rather than os.Exit here) so
	// the deferred cleanup + graceful shutdown below still run.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	srvErr := make(chan error, 1)
	go func() {
		logger.Infof("bridge listening on %s -> gateway %s", cfg.Addr, cfg.GatewayURL)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			srvErr <- err
		}
	}()

	select {
	case <-stop:
		logger.Infof("shutting down")
	case err := <-srvErr:
		logger.Errorf("http server: %v", err)
	}
	shutCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutCtx)
	mgr.Shutdown() // disconnect clients + close the whatsmeow container
}

// Server holds the HTTP handlers' shared dependencies.
type Server struct {
	cfg Config
	mgr *SessionManager
	log waLog.Logger
}

func (s *Server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("POST /session", s.auth(s.handleStartSession))
	mux.HandleFunc("GET /session/{id}", s.auth(s.handleSessionStatus))
	mux.HandleFunc("POST /send", s.auth(s.handleSend))
	mux.HandleFunc("POST /media", s.auth(s.handleMedia))
	mux.HandleFunc("POST /read", s.auth(s.handleRead))
	mux.HandleFunc("POST /call", s.auth(s.handleCall))
	mux.HandleFunc("POST /call/hangup", s.auth(s.handleCallAction))
	mux.HandleFunc("POST /call/answer", s.auth(s.handleCallAction))
	mux.HandleFunc("POST /call/reject", s.auth(s.handleCallAction))
	mux.HandleFunc("GET /calls", s.auth(s.handleCallList))
	mux.HandleFunc("GET /calls/diagnostics", s.auth(s.handleCallDiagnostics))
	mux.HandleFunc("GET /calls/recording", s.auth(s.handleCallRecording))
	mux.HandleFunc("GET /call/audio", s.auth(s.handleCallAudio))
	mux.HandleFunc("GET /calls/events", s.auth(s.handleCallEvents))
	return mux
}

// handleCallEvents returns one call's timeline — what happened, in order, with
// timings. This is what a user reports when a call misbehaves.
func (s *Server) handleCallEvents(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	info, events, err := s.mgr.CallTimeline(q.Get("session"), q.Get("call_id"))
	if err != nil {
		http.Error(w, err.Error(), statusForCall(err))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"call": info, "events": events})
}

// handleCallRecording streams a call's recorded audio. The path comes from the
// registry, never from the request, so this can't be walked into a file read.
func (s *Server) handleCallRecording(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	path, err := s.mgr.RecordingFor(q.Get("session"), q.Get("call_id"))
	if err != nil {
		http.Error(w, err.Error(), statusForMedia(err))
		return
	}
	w.Header().Set("Content-Type", "audio/wav")
	http.ServeFile(w, r, path)
}

// handleCallDiagnostics reports whether an account can place a call, and if
// not, which precondition is missing.
func (s *Server) handleCallDiagnostics(w http.ResponseWriter, r *http.Request) {
	session := r.URL.Query().Get("session")
	if session == "" {
		http.Error(w, "session required", http.StatusBadRequest)
		return
	}
	writeJSON(w, http.StatusOK, s.mgr.CallDiagnostics(session))
}

// handleCall places an outbound call. A `to` places a 1:1 call; a `group_id` or
// two-plus `targets` places a group call.
func (s *Server) handleCall(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Session string   `json:"session"`
		To      string   `json:"to"`
		GroupID string   `json:"group_id"`
		Targets []string `json:"targets"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}
	if body.Session == "" {
		http.Error(w, "session required", http.StatusBadRequest)
		return
	}

	var (
		info callInfo
		err  error
	)
	if body.GroupID != "" || len(body.Targets) > 0 {
		info, err = s.mgr.PlaceGroupCall(r.Context(), body.Session, body.GroupID, body.Targets)
	} else {
		info, err = s.mgr.PlaceCall(r.Context(), body.Session, body.To)
	}
	if err != nil {
		http.Error(w, err.Error(), statusForCall(err))
		return
	}
	writeJSON(w, http.StatusOK, info)
}

// handleCallAction serves hangup/answer/reject — same body, different verb,
// picked off the request path so the three stay in step.
func (s *Server) handleCallAction(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Session string `json:"session"`
		CallID  string `json:"call_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.CallID == "" {
		http.Error(w, "call_id required", http.StatusBadRequest)
		return
	}
	var (
		info callInfo
		err  error
	)
	switch r.URL.Path {
	case "/call/answer":
		info, err = s.mgr.Answer(body.Session, body.CallID)
	case "/call/reject":
		info, err = s.mgr.Reject(body.Session, body.CallID)
	default:
		info, err = s.mgr.Hangup(body.Session, body.CallID)
	}
	if err != nil {
		http.Error(w, err.Error(), statusForCall(err))
		return
	}
	writeJSON(w, http.StatusOK, info)
}

// handleCallList returns tracked calls, optionally filtered to one session.
func (s *Server) handleCallList(w http.ResponseWriter, r *http.Request) {
	calls := s.mgr.ListCalls(r.URL.Query().Get("session"))
	writeJSON(w, http.StatusOK, map[string]any{"calls": calls})
}

// statusForCall maps a call error: unknown call → 404, no session/stack → 409
// (a state precondition the caller can fix by pairing), otherwise 502.
func statusForCall(err error) int {
	switch {
	case errors.Is(err, ErrCallNotFound):
		return http.StatusNotFound
	case errors.Is(err, ErrNotConnected), errors.Is(err, ErrNoCaller):
		return http.StatusConflict
	case errors.Is(err, ErrPlaceTimeout):
		return http.StatusGatewayTimeout
	default:
		return http.StatusBadGateway
	}
}

// auth wraps a handler with the constant-time shared-secret check. An unset
// secret allows everything (local dev).
func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.cfg.Secret != "" {
			got := r.Header.Get("X-Bridge-Secret")
			if subtle.ConstantTimeCompare([]byte(got), []byte(s.cfg.Secret)) != 1 {
				http.Error(w, "bad secret", http.StatusForbidden)
				return
			}
		}
		// Bound request bodies so a runaway payload can't exhaust memory.
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20) // 1 MiB
		next(w, r)
	}
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handleStartSession(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Session string `json:"session"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Session == "" {
		http.Error(w, "session required", http.StatusBadRequest)
		return
	}
	qr, err := s.mgr.StartSession(r.Context(), body.Session)
	if err != nil {
		s.log.Errorf("start session %s: %v", body.Session, err)
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	status, _ := s.mgr.Status(body.Session)
	writeJSON(w, http.StatusOK, map[string]any{"session": body.Session, "status": status, "qr": qr})
}

func (s *Server) handleSessionStatus(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	status, qr := s.mgr.Status(id)
	writeJSON(w, http.StatusOK, map[string]any{"session": id, "status": status, "qr": qr})
}

func (s *Server) handleSend(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Session string `json:"session"`
		To      string `json:"to"`
		Body    string `json:"body"`
		ReplyTo string `json:"reply_to"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}
	id, err := s.mgr.Send(r.Context(), body.Session, body.To, body.Body, body.ReplyTo)
	if err != nil {
		http.Error(w, err.Error(), statusForSend(err))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"id": id})
}

func (s *Server) handleMedia(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Session string `json:"session"`
		MediaID string `json:"media_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}
	data, mime, err := s.mgr.DownloadMedia(r.Context(), body.Session, body.MediaID)
	if err != nil {
		http.Error(w, err.Error(), statusForMedia(err))
		return
	}
	w.Header().Set("Content-Type", mime)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

func (s *Server) handleRead(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Session   string `json:"session"`
		MessageID string `json:"message_id"`
		Chat      string `json:"chat"`
		Sender    string `json:"sender"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, "invalid json", http.StatusBadRequest)
		return
	}
	if body.Chat != "" && body.Sender != "" {
		s.mgr.MarkRead(r.Context(), body.Session, body.Chat, body.Sender, body.MessageID)
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

// statusForSend maps a Send error to a status code: a disconnected account is a
// state precondition (409), everything else is a genuine downstream failure (502).
func statusForSend(err error) int {
	if errors.Is(err, ErrNotConnected) {
		return http.StatusConflict
	}
	return http.StatusBadGateway
}

// statusForMedia maps a DownloadMedia error: unknown media → 404, disconnected
// account → 409, otherwise a downstream failure → 502.
func statusForMedia(err error) int {
	switch {
	case errors.Is(err, ErrMediaNotFound):
		return http.StatusNotFound
	case errors.Is(err, ErrNotConnected):
		return http.StatusConflict
	default:
		return http.StatusBadGateway
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func strPtr(s string) *string    { return &s }
func boolPtr(b bool) *bool       { return &b }
func uint32Ptr(n uint32) *uint32 { return &n }

// reapCalls periodically drops long-ended calls from the in-memory registry so
// a bridge that runs for weeks doesn't retain every call it ever placed, and
// sweeps recorded audio past its retention. A non-positive interval disables
// both.
func reapCalls(mgr *SessionManager, ttl time.Duration, retentionDays uint32) {
	if ttl <= 0 {
		return
	}
	ticker := time.NewTicker(ttl)
	defer ticker.Stop()
	for range ticker.C {
		mgr.calls.reap(ttl)
		mgr.calls.sweepRecordings(retentionDays)
	}
}
