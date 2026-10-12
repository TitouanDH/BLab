from rest_framework.permissions import BasePermission

from . import switch_password

EMAIL_REQUIRED = 'email_required'
SWITCH_PASSWORD_REQUIRED = 'switch_password_required'


class HasEmail(BasePermission):
    """Every account finishes onboarding before anything else: its email, the user's Rainbow
    login (BLab messages them there), then, with BLAB_PROJECTS on, its Switch password.

    Until then, it may only log out and set them (account/, switch_password/): every other
    endpoint refuses with 403 and code 'email_required' or 'switch_password_required',
    admins included. Put it after IsAuthenticated, which answers for anonymous requests.
    """

    message = {'detail': 'Add your email address (your Rainbow login) before going on.', 'code': EMAIL_REQUIRED}
    code = EMAIL_REQUIRED
    needs_switch_password = True

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return True
        if not user.email:
            return False
        if self.needs_switch_password and switch_password.enabled() and not switch_password.has_one(user):
            self.message = {'detail': 'Choose your Switch password before going on.', 'code': SWITCH_PASSWORD_REQUIRED}
            self.code = SWITCH_PASSWORD_REQUIRED
            return False
        return True


class HasEmailOnly(HasEmail):
    """HasEmail, but for the endpoint where the Switch password gets chosen."""

    needs_switch_password = False
