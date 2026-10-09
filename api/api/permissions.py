from rest_framework.permissions import BasePermission

EMAIL_REQUIRED = 'email_required'


class HasEmail(BasePermission):
    """Every account needs an email, the user's Rainbow login (BLab messages them there).

    Until an account has one, it may only log out and set it (account/): every other
    endpoint refuses with 403 and code 'email_required', admins included. Put it after
    IsAuthenticated, which answers for anonymous requests.
    """

    message = {'detail': 'Add your email address (your Rainbow login) before going on.', 'code': EMAIL_REQUIRED}
    code = EMAIL_REQUIRED

    def has_permission(self, request, view):
        user = request.user
        return not (user and user.is_authenticated) or bool(user.email)
