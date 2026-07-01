"use client";

import { useEffect } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="max-w-lg">
      <Alert variant="error">
        <p className="font-medium">Something went wrong</p>
        <p className="mt-1">{error.message}</p>
      </Alert>
      <Button className="mt-4" variant="outline" onClick={reset}>
        Try again
      </Button>
    </div>
  );
}
