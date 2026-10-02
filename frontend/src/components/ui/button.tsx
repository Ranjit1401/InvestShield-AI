import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes, AnchorHTMLAttributes } from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

/**
 * Button variants.
 *
 * `size` is expressed in rem so the control keeps a predictable tap target at
 * every breakpoint.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md font-medium whitespace-nowrap transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-accent text-surface hover:bg-accent-strong focus-visible:outline-accent-strong font-semibold",
        secondary:
          "bg-surface-overlay text-ink hover:bg-hairline border border-hairline",
        outline:
          "border border-hairline bg-transparent text-ink-muted hover:text-ink hover:border-accent-muted hover:bg-surface-raised",
        ghost: "bg-transparent text-ink-muted hover:text-ink hover:bg-surface-raised",
        danger: "bg-tone-danger text-white hover:brightness-110 font-semibold",
      },
      size: {
        sm: "h-8 px-3 text-xs [&_svg]:size-3.5",
        md: "h-10 px-4 text-sm [&_svg]:size-4",
        lg: "h-12 px-6 text-base [&_svg]:size-5",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  },
);

export interface ButtonVariantProps
  extends Omit<VariantProps<typeof buttonVariants>, "size"> {
  size?: "sm" | "md" | "lg";
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & ButtonVariantProps;

export function Button({ className, variant, size, type, ...props }: ButtonProps) {
  return (
    <button
      // Buttons inside forms default to `submit`; being explicit avoids an
      // accidental submit when a button is added to a form later.
      type={type ?? "button"}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  );
}

export type ButtonLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> &
  ButtonVariantProps & { to: string };

/** A link that looks like a button and keeps real link semantics. */
export function ButtonLink({
  className,
  variant,
  size,
  to,
  children,
  ...props
}: ButtonLinkProps) {
  return (
    <Link to={to} className={cn(buttonVariants({ variant, size }), className)} {...props}>
      {children}
    </Link>
  );
}
