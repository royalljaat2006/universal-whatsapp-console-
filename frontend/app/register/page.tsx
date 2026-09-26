import Link from "next/link";

import { AuthShell } from "@/features/auth/auth-shell";
import { RegisterForm } from "@/features/auth/register-form";

export default function RegisterPage() {
  return (
    <AuthShell
      title="Create your workspace"
      description="Set up a new organization — you'll be its owner"
      footer={
        <>
          Already have a workspace?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
          .
        </>
      }
    >
      <RegisterForm />
    </AuthShell>
  );
}
