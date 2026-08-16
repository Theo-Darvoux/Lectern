import { redirect } from "next/navigation";

export default function AdminBulkUsersRedirect() {
  redirect("/staff/users/bulk");
}
