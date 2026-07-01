import { Skeleton } from "@/components/ui/Spinner";

export default function Loading() {
  return (
    <div className="grid grid-cols-1 gap-12 lg:grid-cols-12">
      <div className="space-y-4 lg:col-span-8">
        <Skeleton className="h-12 w-2/3" />
        <Skeleton className="h-6 w-full max-w-xl" />
        <Skeleton className="mt-8 h-96 w-full rounded-xl" />
      </div>
      <div className="lg:col-span-4">
        <Skeleton className="h-[400px] w-full rounded-2xl" />
      </div>
    </div>
  );
}
