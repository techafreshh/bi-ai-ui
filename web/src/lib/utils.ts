import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Verbatim copy of beui.dev's lib/utils (cn helper used by every beUI copy).
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
