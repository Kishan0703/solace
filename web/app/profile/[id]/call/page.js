"use client";

import { useParams } from "next/navigation";
import { CallScreen } from "@/components/PresenzClient";

export default function ProfileCallPage() {
  const params = useParams();
  return <CallScreen profileId={params.id} />;
}
