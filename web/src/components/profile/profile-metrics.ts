export interface ProfileCompletionFields {
  display_name: string | null;
  bio: string | null;
  academic_year: string | null;
  avatar_url: string | null;
}

export function getProfileCompletion(profile: ProfileCompletionFields): number {
  const fields = [
    profile.display_name,
    profile.bio,
    profile.academic_year,
    profile.avatar_url,
  ];
  const completed = fields.filter(
    (value) => typeof value === "string" && value.trim().length > 0,
  ).length;

  return completed * 25;
}

export function getApprovedShare(approved: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((approved / total) * 100)));
}
