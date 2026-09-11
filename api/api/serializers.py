from rest_framework import serializers
from django.contrib.auth.models import User
from .models import HealthCheck, HealthFinding, Switch, Reservation, Port, TemporaryLink

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name']


class SwitchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Switch
        fields = '__all__'


class ReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reservation
        fields = ['id', 'switch', 'user', 'creation_date', 'end_date']

class PortSerializer(serializers.ModelSerializer):
    class Meta:
        model = Port
        fields = '__all__'


class TemporaryLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemporaryLink
        fields = [
            'id', 'port_a', 'port_b', 'reservation', 'created_by', 'svlan',
            'backbone', 'service_name', 'state', 'backbone_verified_at',
            'disconnected_at', 'failure_reason', 'created_at'
        ]
        read_only_fields = fields


class HealthFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthFinding
        fields = [
            'id', 'switch', 'health_check', 'category', 'severity', 'code',
            'message', 'resource', 'observed', 'expected', 'resolved_at', 'created_at'
        ]
        read_only_fields = fields


class HealthCheckSerializer(serializers.ModelSerializer):
    findings = HealthFindingSerializer(many=True, read_only=True)

    class Meta:
        model = HealthCheck
        fields = [
            'id', 'switch', 'requested_by', 'status', 'started_at', 'completed_at',
            'adapter', 'evidence', 'error_message', 'findings'
        ]
        read_only_fields = fields