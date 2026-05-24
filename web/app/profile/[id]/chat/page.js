"use client";

import { useParams } from "next/navigation";
import { ChatWindow } from "@/components/PresenzClient";

export default function ProfileChatPage() {
  const params = useParams();
  return <ChatWindow profileId={params.id} />;
}
