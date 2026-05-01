from simon import normalize_postal, vehicle_speed_model
from unittest.mock import MagicMock

def test_postal_normalization():
    """Verify various postal input formats convert to N-XXX."""
    assert normalize_postal("205") == "N-205"
    assert normalize_postal("P205") == "N-205"
    assert normalize_postal("n-205") == "N-205"
    assert normalize_postal("  205  ") == "N-205"
    assert normalize_postal("random") == "RANDOM"

def test_vehicle_speed_model_scaling():
    """Ensure vehicle speed model applies category and HP factors correctly."""
    base_car = {"bot_category": "car", "horsepower_normalized": 5}
    supercar = {"bot_category": "supercar", "horsepower_normalized": 10}
    truck = {"bot_category": "truck", "horsepower_normalized": 2}
    
    speed_car = vehicle_speed_model(base_car)
    speed_super = vehicle_speed_model(supercar)
    speed_truck = vehicle_speed_model(truck)
    
    assert speed_super > speed_car
    assert speed_truck < speed_car

class TestOperationsPermissions:
    """Verify rank-based permission logic in Operations."""
    
    def test_high_command_check(self):
        from operations import Operations
        cog = Operations(MagicMock())
        
        # Mock Member with CO role
        co_member = MagicMock()
        co_role = MagicMock()
        co_role.name = "[𝐌𝐄𝐓] Commanding Officer"
        co_member.roles = [co_role]
        co_member.id = 123
        
        # Mock Member with Operative role
        op_member = MagicMock()
        op_role = MagicMock()
        op_role.name = "[𝐌𝐄𝐓] Senior Officer"
        op_member.roles = [op_role]
        op_member.id = 456
        
        assert cog._is_high_command(co_member) is True
        assert cog._is_high_command(op_member) is False

    def test_senior_high_command_check(self):
        from operations import Operations
        cog = Operations(MagicMock())
        
        # DCI is High Command but NOT Senior High Command
        dci_member = MagicMock()
        dci_role = MagicMock()
        dci_role.name = "[𝐌𝐄𝐓] Detective Chief Inspector"
        dci_member.roles = [dci_role]
        dci_member.id = 789
        
        assert cog._is_high_command(dci_member) is True
        assert cog._is_senior_high_command(dci_member) is False
