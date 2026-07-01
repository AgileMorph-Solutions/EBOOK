import { Skeleton } from "@/components/ui/Spinner";

export default function BookLoading() {
  return (
    <div className="grid grid-cols-1 gap-gutter md:grid-cols-12">
      <div className="space-y-4 md:col-span-12">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-10 w-3/4" />
      </div>
      <div className="md:col-span-8">
        <Skeleton className="h-64 w-full rounded-xl" />
        <Skeleton className="mt-4 h-40 w-full rounded-lg" />
      </div>
      <div className="space-y-4 md:col-span-4">
        <Skeleton className="h-48 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    </div>
  );
}
