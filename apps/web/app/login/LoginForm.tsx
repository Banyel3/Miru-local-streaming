"use client";

import { FormEvent, useState } from "react";
import { useSearchParams } from "next/navigation";
import { API_PUBLIC } from "@/lib/api";
import { Logo } from "@/components/Logo";

type Stage = "email" | "sent";

/** Email → magic link + OTP. The server answers identically whether or not an
 *  address is invited, so the copy never claims the mail was sent — only that
 *  it will have been, if the address is one of ours. */
export function LoginForm() {
  const params = useSearchParams();
  const expired = params.get("error") === "expired";

  const [stage, setStage] = useState<Stage>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [codeError, setCodeError] = useState(false);

  const requestLink = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true);
    try {
      await fetch(`${API_PUBLIC}/api/auth/request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      setStage("sent");
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    if (code.length !== 6 || busy) return;
    setBusy(true);
    setCodeError(false);
    try {
      const res = await fetch(`${API_PUBLIC}/api/auth/otp`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code }),
      });
      if (res.ok) {
        window.location.href = "/";
        return;
      }
      setCodeError(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex w-full max-w-sm flex-col gap-5 rounded-2xl border border-border bg-surface p-7">
      <div className="flex flex-col gap-2">
        <h1>
          <Logo size="md" />
        </h1>
        <p className="text-[13px] leading-relaxed text-text-dim">
          {stage === "email"
            ? "Sign in with your email. If it's on the list, a link is on its way."
            : "Check your inbox — click the link, or type the 6-digit code here."}
        </p>
      </div>

      {expired && stage === "email" && (
        <p className="rounded-xl border border-border bg-bg-deep/60 px-3.5 py-2.5 text-[12.5px] text-accent">
          That link has expired or was already used. Ask for a fresh one below.
        </p>
      )}

      {stage === "email" ? (
        <form onSubmit={requestLink} className="flex flex-col gap-3">
          <input
            type="email"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            aria-label="Email address"
            className="rounded-xl border border-border bg-bg-deep px-3.5 py-2.5 text-sm text-text outline-none transition-colors focus:border-border-hover"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-xl bg-primary px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-primary-hover disabled:opacity-60"
          >
            {busy ? "Sending…" : "Send sign-in link"}
          </button>
        </form>
      ) : (
        <form onSubmit={submitCode} className="flex flex-col gap-3">
          <input
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            autoFocus
            value={code}
            onChange={(e) => {
              setCode(e.target.value.replace(/\D/g, ""));
              setCodeError(false);
            }}
            placeholder="000000"
            aria-label="Six digit code"
            className="rounded-xl border border-border bg-bg-deep px-3.5 py-2.5 text-center font-mono text-lg tracking-[0.5em] text-text outline-none transition-colors focus:border-border-hover"
          />
          {codeError && (
            <p className="text-[12.5px] text-accent">
              That code didn't work. Five wrong tries kills it — ask for a new
              link if you're out.
            </p>
          )}
          <button
            type="submit"
            disabled={busy || code.length !== 6}
            className="rounded-xl bg-primary px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-primary-hover disabled:opacity-60"
          >
            {busy ? "Checking…" : "Sign in with code"}
          </button>
          <button
            type="button"
            onClick={() => {
              setStage("email");
              setCode("");
            }}
            className="text-[12.5px] font-semibold text-text-muted transition-colors hover:text-text"
          >
            Use a different email
          </button>
        </form>
      )}
    </div>
  );
}
