"use client";

import { CircleCheck, LoaderCircle, MessageSquare, ShieldCheck, Trash2, X } from "lucide-react";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { Verification } from "@/hooks/use-call";

interface VerificationCardProps {
  verification: Verification;
  onSendCode: (email: string) => void;
  onCheckCode: (code: string) => void;
  onDeleteData: () => void;
  onClose: () => void;
}

export function VerificationCard({
  verification,
  onSendCode,
  onCheckCode,
  onDeleteData,
  onClose,
}: VerificationCardProps) {
  const { stage } = verification;
  return (
    <Card aria-label="Returning customer verification">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck aria-hidden className="size-4 text-primary" />
          Returning customer
        </CardTitle>
        <CardDescription>
          Verifying lets Maya see a short summary of your last visit. Nothing else.
        </CardDescription>
        {stage !== "verified" && (
          <CardAction>
            <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
              <X />
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {(stage === "email" || stage === "expired") && (
          <EmailForm verification={verification} onSendCode={onSendCode} />
        )}
        {stage === "code" && <CodeForm verification={verification} onCheckCode={onCheckCode} />}
        {stage === "verified" && (
          <Verified verification={verification} onDeleteData={onDeleteData} />
        )}
        {stage === "failed" && (
          <p role="alert" className="text-sm text-destructive">
            {verification.message}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function EmailForm({
  verification,
  onSendCode,
}: {
  verification: Verification;
  onSendCode: (email: string) => void;
}) {
  const [email, setEmail] = useState("");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (email.trim()) onSendCode(email.trim());
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      {verification.message && (
        <p role="alert" className="text-sm text-muted-foreground">
          {verification.message}
        </p>
      )}
      <label htmlFor="verify-email" className="text-sm font-medium">
        Email on file
      </label>
      <div className="flex gap-2">
        <Input
          id="verify-email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
        />
        <Button type="submit" disabled={verification.submitting || !email.trim()}>
          {verification.submitting && <LoaderCircle className="motion-safe:animate-spin" />}
          Send code
        </Button>
      </div>
    </form>
  );
}

function CodeForm({
  verification,
  onCheckCode,
}: {
  verification: Verification;
  onCheckCode: (code: string) => void;
}) {
  const [code, setCode] = useState("");
  const expires = verification.expiresAt
    ? new Date(verification.expiresAt).toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
      })
    : null;
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (/^\d{6}$/.test(code)) onCheckCode(code);
  }
  return (
    <>
      <div className="flex gap-3 rounded-2xl rounded-tl-sm bg-muted px-3.5 py-2.5 text-sm">
        <MessageSquare aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div>
          <p className="text-xs text-muted-foreground">
            Text message to the phone on file for {verification.maskedEmail}
          </p>
          <p>
            Northstar: your verification code is{" "}
            <span className="font-mono font-semibold tracking-wider">{verification.demoCode}</span>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Demo only: a real system would text this to you, not show it here.
          </p>
        </div>
      </div>
      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor="verify-code" className="text-sm font-medium">
          Enter the 6-digit code{expires ? ` (expires at ${expires})` : ""}
        </label>
        <div className="flex gap-2">
          <Input
            id="verify-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
            className="font-mono tracking-widest"
          />
          <Button type="submit" disabled={verification.submitting || code.length !== 6}>
            {verification.submitting && <LoaderCircle className="motion-safe:animate-spin" />}
            Verify
          </Button>
        </div>
        {verification.message && (
          <p role="alert" className="text-sm text-destructive">
            {verification.message}
          </p>
        )}
      </form>
    </>
  );
}

function Verified({
  verification,
  onDeleteData,
}: {
  verification: Verification;
  onDeleteData: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  if (!verification.returningCustomer) {
    return (
      <p className="flex items-center gap-2 text-sm">
        <CircleCheck aria-hidden className="size-4 text-success" />
        Code confirmed. There are no previous visits on file for that email.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-center gap-2 text-sm">
        <CircleCheck aria-hidden className="size-4 text-success" />
        Verified. Maya can now see a short summary of your last visit.
      </p>
      {confirming ? (
        <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 p-3">
          <p className="text-sm">
            Delete everything Northstar stored about you: past calls, transcripts, bookings and
            your CRM record? This can&rsquo;t be undone.
          </p>
          <div className="flex gap-2">
            <Button variant="destructive" onClick={onDeleteData}>
              <Trash2 />
              Delete my data
            </Button>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Keep it
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="outline" className="self-start" onClick={() => setConfirming(true)}>
          <Trash2 />
          Delete my data
        </Button>
      )}
    </div>
  );
}
