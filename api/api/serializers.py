from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Switch, Reservation, Port
from .reservations import renewals_left

class UserSerializer(serializers.ModelSerializer):
    """A user as any logged-in user may see them: id and username. The password is only written, at signup."""

    class Meta:
        model = User
        fields = ['id', 'username', 'password']
        extra_kwargs = {'password': {'write_only': True}}

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