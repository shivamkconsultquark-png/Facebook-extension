// Harness stubs for the main app's Skeleton components.
import React from 'react';

export function Skeleton({ width = '100%', height = 12, rounded = false }) {
  return <div className="bg-zinc-200 animate-pulse" style={{ width, height, borderRadius: rounded ? 9999 : 6 }} />;
}
export function SkeletonText({ lines = 3, lastWidth = '60%' }) {
  return <div className="flex flex-col gap-2">{Array.from({ length: lines }).map((_, i) => <Skeleton key={i} width={i === lines - 1 ? lastWidth : '100%'} />)}</div>;
}
export function SkeletonHeader() {
  return <div className="mb-6 flex flex-col gap-2"><Skeleton width="220px" height={24} /><Skeleton width="320px" /></div>;
}
