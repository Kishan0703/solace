"use client";

import { useParams } from "next/navigation";
import { MemoryTimeline, useProfile } from "@/components/PresenzClient";

export default function ProfileMemoriesPage() {
  const params = useParams();
  const { memories } = useProfile(params.id);
  return <MemoryTimeline memories={memories} />;
}
