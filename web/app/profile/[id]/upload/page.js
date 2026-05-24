"use client";

import { useParams } from "next/navigation";
import { UploadZone } from "@/components/PresenzClient";

export default function ProfileUploadPage() {
  const params = useParams();
  return <UploadZone profileId={params.id} />;
}
