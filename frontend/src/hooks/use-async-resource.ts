import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError} from "@/services/api-client";
import { toPublicApiError } from "@/services/api-client";

/**
 * A minimal async-resource hook.
 *
 * The project has no demonstrated need for a server-state library, so this is
 * plain React state. It covers the three states every API-driven view needs
 * (loading / error / data) and cancels the in-flight request when the inputs
 * change or the component unmounts, so a slow response cannot overwrite newer
 * state.
 */
export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  isLoading: boolean;
  /** True only for the first load, so refreshes do not flash a skeleton. */
  isInitialLoading: boolean;
  reload: () => void;
}

export interface UseAsyncResourceOptions {
  /** Skip the request entirely, e.g. while a route parameter is still empty. */
  enabled?: boolean;
}

export function useAsyncResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: UseAsyncResourceOptions = {},
): AsyncState<T> {
  const { enabled = true } = options;

  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(enabled);
  const [isInitialLoading, setIsInitialLoading] = useState<boolean>(enabled);
  const [reloadToken, setReloadToken] = useState(0);

  // Keep the latest loader without making it a dependency, so callers can pass
  // an inline closure without retriggering the effect on every render.
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    if (!enabled) {
      setIsLoading(false);
      setIsInitialLoading(false);
      return;
    }

    const controller = new AbortController();
    let active = true;

    setIsLoading(true);
    setIsInitialLoading((previous) => previous || data === null);
    setError(null);

    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (!active) return;
        setData(result);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        // An abort is a consequence of navigating away, not a failure to report.
        if (controller.signal.aborted) return;
        setData(null);
        setError(toPublicApiError(cause));
      })
      .finally(() => {
        if (!active) return;
        setIsLoading(false);
        setIsInitialLoading(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
    // `data` is read only to decide skeleton visibility on the first load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, reloadToken]);

  const reload = useCallback(() => {
    setReloadToken((token) => token + 1);
  }, []);

  return { data, error, isLoading, isInitialLoading, reload };
}
