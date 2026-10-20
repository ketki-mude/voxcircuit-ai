"use client";

import { Check, LoaderCircle, X } from "lucide-react";
import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { Ticket } from "@/hooks/use-call";

interface ApprovalTicketProps {
  ticket: Ticket;
  onDecide: (approvalId: string, decision: "approve" | "cancel") => void;
}

export function ApprovalTicket({ ticket, onDecide }: ApprovalTicketProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [ticket.approvalId]);
  const expires = new Date(ticket.expiresAt).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
  return (
    <Card
      ref={ref}
      aria-label="Booking ticket"
      className="scroll-mt-4 border-warning/50 ring-1 ring-warning/30"
    >
      <CardHeader>
        <CardTitle>
          {ticket.action === "reschedule" ? "Review before I reschedule" : "Review before I book"}
        </CardTitle>
        <CardDescription>
          Nothing is booked until you press Approve. This ticket expires at {expires}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
          {ticket.rows.map((row) => (
            <div key={row.label} className="contents">
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd className="font-medium break-words">{row.value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button
          size="lg"
          disabled={ticket.submitting}
          onClick={() => onDecide(ticket.approvalId, "approve")}
        >
          {ticket.submitting ? <LoaderCircle className="motion-safe:animate-spin" /> : <Check />}
          {ticket.submitting ? "Working on it" : "Approve"}
        </Button>
        <Button
          size="lg"
          variant="outline"
          disabled={ticket.submitting}
          onClick={() => onDecide(ticket.approvalId, "cancel")}
        >
          <X />
          Cancel
        </Button>
      </CardFooter>
    </Card>
  );
}
