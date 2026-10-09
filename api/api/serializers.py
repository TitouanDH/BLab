from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Switch, Reservation, Port
from .reservations import renewals_left

class UserSerializer(serializers.ModelSerializer):
    """A user as any logged-in user may see them: id and username."""

    class Meta:
        model = User
        fields = ['id', 'username']


class AccountSerializer(serializers.ModelSerializer):
    """A user's own account, as only they see it: with their email, the Rainbow login BLab messages them on.

    Signup writes all of it (the password only ever written); afterwards only the email changes.
    The email is required, stored lowercase, and used by no other account whatever its case.
    """

    email = serializers.EmailField(max_length=254)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']
        extra_kwargs = {'password': {'write_only': True}}

    def validate_email(self, value):
        value = value.lower()
        others = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError('Another account already uses this email address.')
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class SwitchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Switch
        fields = '__all__'


class ReservationSerializer(serializers.ModelSerializer):
    renewals_left = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = ['id', 'switch', 'user', 'creation_date', 'end_date', 'renewals', 'renewals_left', 'admin_exception']

    def get_renewals_left(self, reservation):
        return renewals_left(reservation)

class PortSerializer(serializers.ModelSerializer):
    class Meta:
        model = Port
        fields = '__all__'