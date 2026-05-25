import { useEffect, useState } from "react";
import { apiFetch } from "./api-client";
import { MAX_ANSWERS_PER_QUESTION, MAX_QUESTIONS_PER_QCM } from "./qcm-types";

export interface QcmLimits {
    max_answers_per_question: number;
    max_questions_per_qcm: number;
}

const DEFAULTS: QcmLimits = {
    max_answers_per_question: MAX_ANSWERS_PER_QUESTION,
    max_questions_per_qcm: MAX_QUESTIONS_PER_QCM,
};

let cached: QcmLimits | null = null;
let fetchPromise: Promise<QcmLimits> | null = null;

export async function fetchQcmLimits(): Promise<QcmLimits> {
    if (cached) return cached;
    if (!fetchPromise) {
        fetchPromise = apiFetch<QcmLimits>("/qcm/limits", { skipAuth: true })
            .then((data) => {
                cached = data;
                return data;
            })
            .catch(() => DEFAULTS)
            .finally(() => { fetchPromise = null; });
    }
    return fetchPromise;
}

export function useQcmLimits(): QcmLimits {
    const [limits, setLimits] = useState<QcmLimits>(cached ?? DEFAULTS);

    useEffect(() => {
        if (cached) return;
        fetchQcmLimits().then(setLimits);
    }, []);

    return limits;
}
