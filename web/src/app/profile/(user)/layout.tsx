import { ProfilePageContent } from "@/components/profile/profile-page-content";

// Sits above the [id] segment, so it stays mounted (and keeps its state) when
// the user moves from one profile to another. The page reads the id with
// usePathname. The (user) group keeps it off the user's own /profile page.
export default function ProfileLayout() {
  return <ProfilePageContent />;
}
